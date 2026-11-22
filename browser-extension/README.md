# Notewell recorder helper

This Chrome extension notes the active tab address, console warnings and errors,
and failed or slow web requests while a Notewell screen recording is running.

It talks only to Notewell on this computer (`127.0.0.1`), records nothing when
no screen recording is active, stays off in Incognito windows, and never reads
cookies, headers, form fields, request bodies, or page text.

## How to load it in Chrome

1. Open Chrome, type `chrome://extensions` in the address bar, and press Enter.
2. Turn on **Developer mode** using the switch in the top-right corner.
3. Click **Load unpacked** in the top-left corner.
4. Select the `browser-extension` folder inside your Notewell folder and click **Select Folder**.
5. Check that **Notewell recorder helper** appears in the list and is turned on. Leave **Allow in Incognito** off.

## Options

To switch console messages or network requests on or off, open `chrome://extensions`,
click **Details** on **Notewell recorder helper**, and open **Extension options**.
