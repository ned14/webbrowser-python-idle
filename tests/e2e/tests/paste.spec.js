import { expect, test } from '../lib/browser.js';
import { waitForLightDesktop } from '../lib/desktop.js';

// Clipboard panel paste E2E (plans: the guest paste-typer types CXCLIP
// frames into the focused X window via XTEST — XTestFakeKeyEvent through
// libXtst/ctypes; xdotool is BANNED, it breaks the image, AGENTS.md).
//
// THE PAGE NEVER CLAIMS THE HOST CLIPBOARD API: no navigator.clipboard
// anywhere, no permission prompts. Host -> VM paste goes through the
// sidebar Clipboard panel's textarea:
//   (a) untypable text (non-ASCII, control chars) is REFUSED page-side
//       with a diagnostic and nothing is sent;
//   (b) typable text is sent as a CXCLIP frame over the console channel
//       to the guest paste-typer, which TYPES it into the focused guest
//       window — validated here END-TO-END by pasting into the file
//       explorer's Search box and watching the file list filter: the
//       search text appears in the entry box and the list empties.

const SITE_URL =
	process.env.E2E_SITE_URL ||
	`https://127.0.0.1:${process.env.E2E_SITE_PORT || 8081}/alpine.html`;

async function termText(page) {
	return page.evaluate(() => {
		const t = window.__webvmTerm;
		if (!t) return '';
		const out = [];
		const buf = t.buffer.active;
		for (let y = 0; y < buf.length; y++) {
			const line = buf.getLine(y);
			out.push(line ? line.translateToString(true) : '');
		}
		return out.join('\n');
	});
}

async function openClipboardPanel(page) {
	// Hover, not click: the sidebar's design is hover-to-open / click-to-
	// close (clicking an already-hovered icon dismisses the panel).
	await page.locator('[aria-label="Clipboard"]').hover();
	await expect(page.locator('h1.text-lg')).toContainText('Clipboard');
}

async function clipboardPanelOpen(page) {
	return page
		.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]')
		.isVisible()
		.catch(() => false);
}

async function closeClipboardPanel(page) {
	// Clicking the icon TOGGLES the panel; only click when it is open so a
	// closed panel is not opened.
	if (await clipboardPanelOpen(page)) {
		await page.locator('[aria-label="Clipboard"]').click();
	}
}

// Count the light text-row bands in the file list area (y 195..830) of the
// KMS canvas. An empty folder/search result renders no rows -> 0.
async function listRowCount(page) {
	return page.evaluate(() => {
		const d = document.getElementById('display');
		if (!d || !d.width || !d.height) return -1;
		const s = document.createElement('canvas');
		s.width = d.width;
		s.height = d.height;
		const c = s.getContext('2d');
		c.drawImage(d, 0, 0);
		const data = c.getImageData(0, 0, s.width, s.height).data;
		let bands = 0;
		let inBand = false;
		for (let y = 195; y < 830; y += 4) {
			let light = 0;
			for (let x = 40; x < 700; x += 8) {
				const i = (y * s.width + x) * 4;
				if (data[i] > 120 && data[i + 1] > 120 && data[i + 2] > 120) light++;
			}
			const isRow = light > 5; // a row of text spans horizontally
			if (isRow && !inBand) {
				bands++;
				inBand = true;
			}
			if (!isRow) inBand = false;
		}
		return bands;
	});
}

test('untypable text is refused with a diagnostic and nothing is sent', async ({
	page,
}) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await openClipboardPanel(page);

	// The refusal happens page-side, before any guest interaction — it must
	// work even while the VM is still booting.
	await page
		.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]')
		.fill('café — 日本語');
	await page.locator('[title="Send the box above into the VM"]').click();

	await expect(page.locator('p.text-amber-400')).toContainText(
		'Not pasted — cannot be typed as keys: char U+00E9'
	);
});

test('pasted text definitely appears in a text entry box in the VM (explorer Search box)', async ({
	page,
}) => {
	test.setTimeout(480_000);
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await waitForLightDesktop(page);

	// The home folder lists at least one entry ("examples"): baseline rows.
	const baseline = await listRowCount(page);
	expect(baseline).toBeGreaterThan(0);

	// Focus the explorer's Search entry (click well right of the sidebar
	// panel zone — x=700 is safely inside the entry and outside the panel
	// overlay). The entry's y is swept across the search row because the
	// exact pixel height depends on fonts; the first hit sticks.
	const SEARCH_TEXT = 'zzzz-no-match-xyz'; // matches nothing -> list empties
	const attempts = [150, 158, 166, 174];
	let delivered = false;
	for (const y of attempts) {
		await closeClipboardPanel(page);
		await page.mouse.click(700, y, { delay: 40 });
		await openClipboardPanel(page);
		await page
			.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]')
			.fill(SEARCH_TEXT);
		await page.locator('[title="Send the box above into the VM"]').click();

		// The guest typer acked the frame on the console.
		const acked = await expect
			.poll(async () => termText(page), { timeout: 30_000, intervals: [2000] })
			.toContain(`CXACK ${new TextEncoder().encode(SEARCH_TEXT).length}`)
			.then(() => true)
			.catch(() => false);
		if (!acked) continue;

		// THE delivery assertion: the Search box received the typed text,
		// so the file list filtered down to nothing.
		const rows = await expect
			.poll(async () => listRowCount(page), { timeout: 20_000, intervals: [1000] })
			.toBe(0)
			.then(() => true)
			.catch(() => false);
		if (rows) {
			delivered = true;
			break;
		}
	}
	expect(delivered).toBe(true);
});

test('file content can be loaded into the paste box (Open file… and drag-and-drop), with a length warning', async ({
	page,
}) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await openClipboardPanel(page);
	const box = page.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]');

	// 1. Open file… button loads a file's text content into the box.
	await page.locator('input[type="file"]').setInputFiles({
		name: 'hello.txt',
		mimeType: 'text/plain',
		buffer: Buffer.from('file content says hello', 'utf-8'),
	});
	await expect(box).toHaveValue('file content says hello');

	// 2. Drag-and-drop a file onto the textarea replaces the content.
	const dt = await page.evaluateHandle(() => {
		const d = new DataTransfer();
		d.items.add(new File(['dropped by drag'], 'dropped.txt', { type: 'text/plain' }));
		return d;
	});
	await box.dispatchEvent('drop', { dataTransfer: dt });
	await expect(box).toHaveValue('dropped by drag');

	// 3. Long content shows the typing-time warning (no paste click needed).
	await box.fill('x'.repeat(500));
	await expect(page.locator('p.text-amber-400, span.text-amber-400').last()).toContainText(
		'chars — ~5s to type'
	);
});

test('Open file… does not close the paste pane or reset the loaded content (hover-close regression)', async ({
	page,
}) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await openClipboardPanel(page);
	const box = page.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]');

	// Click Open file… and hold the file chooser open.
	const [chooser] = await Promise.all([
		page.waitForEvent('filechooser'),
		page.locator('button:has-text("Open file")').click(),
	]);

	// The user moves the mouse over to the file dialog to choose a file —
	// i.e. the pointer leaves the sidebar panel. The panel's hover-close
	// (400 ms) must NOT fire: without the filePickerActive guard this
	// unmounts PasteTab and drops the file content.
	await page.mouse.move(700, 400);
	await page.waitForTimeout(700); // comfortably past the 400 ms close timer

	// Pick the file; the dialog closes and the browser fires a synthetic
	// mouseleave (pointer still away from the panel).
	await chooser.setFiles({
		name: 'picked.txt',
		mimeType: 'text/plain',
		buffer: Buffer.from('content from the picker', 'utf-8'),
	});

	// The panel must STILL be open and the file content loaded into the box.
	await expect(box).toBeVisible();
	await expect(box).toHaveValue('content from the picker');

	// The filePickerActive guard must not linger: a genuine hover-away now
	// closes the panel normally. (Wait past the guard window first; the
	// pointer is already outside the panel, so re-enter and leave.)
	await page.waitForTimeout(900);
	await page.mouse.move(200, 300); // back over the panel
	await page.waitForTimeout(100);
	await page.mouse.move(700, 400); // leave again
	await page.waitForTimeout(700); // past the 400 ms close timer
	await expect(box).not.toBeVisible();
});

test('file dialog open + mouse moved away for a long dwell keeps the panel open (hover-close regression, long version)', async ({
	page,
}) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await openClipboardPanel(page);
	const box = page.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]');

	// Open the file dialog.
	const [chooser] = await Promise.all([
		page.waitForEvent('filechooser'),
		page.locator('button:has-text("Open file")').click(),
	]);

	// The user takes a while to find the file in the dialog — the pointer
	// stays over the panel for a LONG dwell FIRST, well past any short-
	// lived guard window. (A timed guard would already have expired.)
	await page.waitForTimeout(1500);

	// Now the user moves the mouse over to the dialog: this fires the
	// panel's mouseleave. With a timed guard this mouseleave arrives AFTER
	// the guard cleared and closes the panel (the reported bug).
	await page.mouse.move(700, 400);
	await page.waitForTimeout(900);

	// The panel must STILL be open while the dialog is up.
	await expect(box).toBeVisible();

	// Choose the file — the dialog closes and the browser fires a synthetic
	// mouseleave (pointer still away from the panel).
	await chooser.setFiles({
		name: 'chosen.txt',
		mimeType: 'text/plain',
		buffer: Buffer.from('chosen after long dwell', 'utf-8'),
	});

	// The panel is still open and the content loaded.
	await expect(box).toBeVisible();
	await expect(box).toHaveValue('chosen after long dwell');

	// The guard cleared when the picker closed: a genuine hover-away now
	// closes the panel normally.
	await page.mouse.move(200, 300); // back over the panel
	await page.waitForTimeout(100);
	await page.mouse.move(700, 400); // leave again
	await page.waitForTimeout(700); // past the 400 ms close timer
	await expect(box).not.toBeVisible();
});

test('a window focus event while the file dialog is open must not clear the picker guard (wait-then-move regression)', async ({
	page,
}) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await openClipboardPanel(page);
	const box = page.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]');

	// Open the file dialog.
	const [chooser] = await Promise.all([
		page.waitForEvent('filechooser'),
		page.locator('button:has-text("Open file")').click(),
	]);

	// The user waits with the dialog open. Chrome fires a window `focus`
	// event during this (focus churn when the native picker opens) — it
	// must NOT clear the picker guard. (Before the fix, this cleared the
	// guard, so the later mouseleave closed the panel.)
	await page.waitForTimeout(1200);
	await page.evaluate(() => window.dispatchEvent(new Event('focus')));

	// Now the user moves the mouse over to the dialog: the panel's
	// mouseleave fires — with the guard intact the panel must NOT close.
	await page.mouse.move(700, 400);
	await page.waitForTimeout(900);
	await expect(box).toBeVisible();

	// Pick the file: the dialog closes; the panel stays open with content.
	await chooser.setFiles({
		name: 'picked.txt',
		mimeType: 'text/plain',
		buffer: Buffer.from('survives the focus event', 'utf-8'),
	});
	await expect(box).toBeVisible();
	await expect(box).toHaveValue('survives the focus event');

	// The guard cleared when the picker closed (change event): a genuine
	// hover-away now closes the panel normally.
	await page.mouse.click(200, 300); // re-enter the panel area
	await page.mouse.move(700, 400); // leave again
	await page.waitForTimeout(700); // past the 400 ms close timer
	await expect(box).not.toBeVisible();
});

test('length warning updates immediately while typing and after opening a file', async ({
	page,
}) => {
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	await openClipboardPanel(page);
	const box = page.locator('textarea[placeholder="Type, paste, or drop a file here, then click Paste"]');
	const warning = page.locator('span.text-amber-400'); // the length note
	const hint = page.locator('span.text-gray-500'); // "or drag a file onto the box"

	// Short content: no warning, hint shown.
	await box.fill('short');
	await expect(hint).toBeVisible();
	await expect(warning).toHaveCount(0);

	// Typing past the threshold shows the warning IMMEDIATELY (the note is
	// reactive — a bare template call would never re-render here).
	await box.fill('x'.repeat(500));
	await expect(warning).toContainText('500 chars — ~5s to type');

	// It updates as the content grows.
	await box.fill('x'.repeat(900));
	await expect(warning).toContainText('900 chars — ~9s to type');

	// Over the hard cap: the too-long note.
	await box.fill('x'.repeat(10001));
	await expect(warning).toContainText('too long (max 10000)');

	// Opening a SHORT file clears the warning immediately.
	await page.locator('input[type="file"]').setInputFiles({
		name: 'small.txt',
		mimeType: 'text/plain',
		buffer: Buffer.from('tiny', 'utf-8'),
	});
	await expect(box).toHaveValue('tiny');
	await expect(hint).toBeVisible();
	await expect(warning).toHaveCount(0);

	// Opening a LONG file shows the warning immediately.
	await page.locator('input[type="file"]').setInputFiles({
		name: 'big.txt',
		mimeType: 'text/plain',
		buffer: Buffer.from('y'.repeat(600), 'utf-8'),
	});
	await expect(box).toHaveValue('y'.repeat(600));
	await expect(warning).toContainText('600 chars — ~6s to type');
});

test('Ctrl+V during boot is left to the guest: no error, no crash, desktop comes up', async ({
	page,
}) => {
	const consoleErrors = [];
	page.on('console', (msg) => {
		if (msg.type() !== 'error') return;
		if (/log\.tailscale\.com/.test(msg.text())) return;
		if (/Content Security Policy/.test(msg.text()) && /connect-src/.test(msg.text()))
			return;
		consoleErrors.push(msg.text());
	});
	await page.goto(SITE_URL, { waitUntil: 'domcontentloaded' });
	// Press while the VM is still booting (no ready marker yet).
	await page.keyboard.press('Control+V');
	// The page never intercepts Ctrl+V; just confirm nothing breaks and the
	// desktop still comes up.
	await waitForLightDesktop(page);
	expect(await page.locator('[role="alert"]').count()).toBe(0);
	expect(consoleErrors).toEqual([]);
});
