/*
 * icon-probe.c - test GTK STOCK-ICON loading in the main thread under CheerpX
 * (plans/display-bug.md §2.9). gtk-hello (no menu bar, no stock icons) works;
 * both pcmanfm and spacefm stall right after GTK loads stock icons for their
 * menu bars (fontconfig + hicolor index.theme scans, then a silent futex
 * wait). This probe builds a window with a menu bar of stock icons — if it
 * also stalls before showing the window (or the window never fills the
 * canvas), GTK stock-icon loading is the blocker.
 *
 * Build (i386 musl, Alpine 3.17):
 *   apk add gcc musl-dev pkgconf gtk+3.0-dev
 *   gcc -O2 -o icon-probe icon-probe.c $(pkg-config --cflags --libs gtk+-3.0)
 */
#include <stdio.h>
#include <gtk/gtk.h>

int main(int argc, char **argv)
{
	gtk_init(&argc, &argv);
	fprintf(stderr, "ICON-PROBE-START\n");
	fflush(stderr);

	GtkWidget *win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
	gtk_window_set_title(GTK_WINDOW(win), "Icon Probe");
	gtk_window_set_default_size(GTK_WINDOW(win), 600, 400);
	fprintf(stderr, "window created\n");
	fflush(stderr);

	GtkWidget *box = gtk_vbox_new(FALSE, 0);
	GtkWidget *menubar = gtk_menu_bar_new();
	GtkWidget *menu = gtk_menu_new();
	GtkWidget *root = gtk_menu_item_new_with_label("File");
	GtkWidget *item;
	int i;
	const char *stocks[] = { GTK_STOCK_OPEN, GTK_STOCK_SAVE, GTK_STOCK_CUT,
		GTK_STOCK_COPY, GTK_STOCK_PASTE, GTK_STOCK_DELETE,
		GTK_STOCK_NEW, GTK_STOCK_PROPERTIES, NULL };
	for (i = 0; stocks[i]; i++)
	{
		fprintf(stderr, "menu item %s ENTER\n", stocks[i]);
		fflush(stderr);
		item = gtk_image_menu_item_new_from_stock(stocks[i], NULL);
		gtk_menu_shell_append(GTK_MENU_SHELL(menu), item);
		fprintf(stderr, "menu item %s RET\n", stocks[i]);
		fflush(stderr);
	}
	gtk_menu_item_set_submenu(GTK_MENU_ITEM(root), menu);
	gtk_menu_shell_append(GTK_MENU_SHELL(menubar), root);
	gtk_box_pack_start(GTK_BOX(box), menubar, FALSE, TRUE, 0);
	gtk_container_add(GTK_CONTAINER(win), box);

	fprintf(stderr, "gtk_widget_show_all ENTER\n");
	fflush(stderr);
	gtk_widget_show_all(win);
	fprintf(stderr, "gtk_widget_show_all RET\n");
	fflush(stderr);

	fprintf(stderr, "gtk_main ENTER\n");
	fflush(stderr);
	gtk_main();
	fprintf(stderr, "ICON-PROBE-EXIT\n");
	return 0;
}
