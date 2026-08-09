// Self-hosted CheerpX runtime entry.
//
// The pinned `@leaningtech/cheerpx` npm package is only a thin wrapper that
// dynamic-imports its core from https://cxrtnc.leaningtech.com/<version>/ by
// default — an EXTERNAL request the site must not make. This module loads the
// SAME pinned 1.3.7 runtime from our own origin instead (webvm/cheerpx/, copied
// into the served build by viteStaticCopy). The runtime's own modules resolve
// relatively against /cheerpx/cx.esm.js, so everything stays same-origin.
//
// The dynamic import goes through `new Function` so the bundler never rewrites
// the URL (the browser resolves "/cheerpx/cx.esm.js" against the page origin).
const VERSION = "1.3.7";
const dynImport = new Function("x", "return import(x)");
const CheerpX = await dynImport("/cheerpx/cx.esm.js");

export const Linux = CheerpX.Linux;
export const HttpBytesDevice = CheerpX.HttpBytesDevice;
export const CloudDevice = CheerpX.CloudDevice;
export const GitHubDevice = CheerpX.GitHubDevice;
export const IDBDevice = CheerpX.IDBDevice;
export const WebDevice = CheerpX.WebDevice;
export const DataDevice = CheerpX.DataDevice;
export const OverlayDevice = CheerpX.OverlayDevice;
export const System = CheerpX.System;
export const TailscaleNetwork = CheerpX.TailscaleNetwork;
export const DirectSocketsNetwork = CheerpX.DirectSocketsNetwork;

// Keep the pinned version visible in the build output.
export { VERSION };
