r"""Always-on-top window showing the transcript as the meeting happens.

Run it on its own to see it without a meeting:

    venv\Scripts\python.exe live_window.py --demo

Why tkinter
-----------
It is in the standard library, so the recorder gains a window and no new
dependency. Qt would look better and would be a large new package on the
machine's most fragile application; scrolling text does not justify that.

Threading
---------
pystray owns the main thread, so Tk runs on its own thread with its own
mainloop. Tk is not thread-safe, so every widget call happens on that thread:
other threads only put messages on a queue, which the window drains from a
`after()` timer. Nothing outside this file touches a widget.

The text here is disposable. It is never saved, never uploaded, and never
compared against the real transcript, which is still produced by the accurate
pass at the end of the meeting.
"""
import logging
import queue
import threading

log = logging.getLogger("recorder")

_BG = "#14141e"
_FG = "#e8e8f0"
_DIM = "#7a7a8c"
_ME = "#7fd1ff"
_THEM = "#ffd479"


class LiveWindow:
    """A scrolling transcript window driven from any thread via `append()`."""

    def __init__(self, title="Meeting - live transcript", topmost=True,
                 width=520, height=380):
        self.title = title
        self.topmost = topmost
        self.width = width
        self.height = height
        self._q = queue.Queue()
        self._thread = None
        self._root = None
        self._text = None
        self._status = None
        self._running = threading.Event()

    # ---- called from any thread -------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="live-window")
        self._thread.start()

    def append(self, text, speaker=None):
        self._q.put(("line", speaker, text))

    def set_status(self, text):
        self._q.put(("status", None, text))

    def clear(self):
        self._q.put(("clear", None, None))

    def close(self):
        self._running.clear()
        self._q.put(("close", None, None))

    def is_open(self):
        return bool(self._thread and self._thread.is_alive())

    # ---- everything below runs on the window thread -----------------
    def _run(self):
        try:
            import tkinter as tk
        except Exception as e:
            log.warning("live window unavailable (no tkinter): %s", e)
            self._running.clear()
            return
        try:
            root = tk.Tk()
            self._root = root
            root.title(self.title)
            root.geometry(f"{self.width}x{self.height}")
            root.configure(bg=_BG)
            root.attributes("-topmost", bool(self.topmost))
            root.protocol("WM_DELETE_WINDOW", self._on_user_close)

            frame = tk.Frame(root, bg=_BG)
            frame.pack(fill="both", expand=True, padx=8, pady=(8, 0))
            scroll = tk.Scrollbar(frame)
            scroll.pack(side="right", fill="y")
            text = tk.Text(frame, bg=_BG, fg=_FG, wrap="word", bd=0,
                           font=("Segoe UI", 10), yscrollcommand=scroll.set,
                           insertbackground=_FG, padx=6, pady=6)
            text.pack(side="left", fill="both", expand=True)
            scroll.config(command=text.yview)
            text.tag_config("me", foreground=_ME)
            text.tag_config("them", foreground=_THEM)
            text.tag_config("dim", foreground=_DIM)
            text.config(state="disabled")
            self._text = text

            status = tk.Label(root, text="starting...", bg=_BG, fg=_DIM,
                              anchor="w", font=("Segoe UI", 8))
            status.pack(fill="x", padx=14, pady=(2, 6))
            self._status = status

            root.after(150, self._drain)
            root.mainloop()
            # destroy() must happen on the thread that created Tk. Doing it
            # here, after mainloop returns, is what stops the interpreter
            # tearing the Tcl interpreter down from the main thread and
            # printing "Tcl_AsyncDelete: async handler deleted by the wrong
            # thread" - which on exit can take the process with it.
            try:
                root.destroy()
            except Exception:
                pass
        except Exception as e:
            log.warning("live window crashed: %s", e)
        finally:
            self._running.clear()
            self._root = None
            self._text = None
            self._status = None

    def _on_user_close(self):
        # Closing the window must not stop the recording, only the view of it.
        self._running.clear()
        try:
            self._root.quit()
        except Exception:
            pass

    def _drain(self):
        try:
            while True:
                kind, speaker, payload = self._q.get_nowait()
                if kind == "close":
                    # quit() ends mainloop; _run() then destroys Tk on this
                    # same thread. Never destroy from here.
                    self._root.quit()
                    return
                if kind == "status":
                    self._status.config(text=payload)
                elif kind == "clear":
                    self._text.config(state="normal")
                    self._text.delete("1.0", "end")
                    self._text.config(state="disabled")
                elif kind == "line":
                    at_bottom = self._text.yview()[1] > 0.999
                    self._text.config(state="normal")
                    if speaker:
                        tag = "me" if speaker.lower() == "me" else "them"
                        self._text.insert("end", f"{speaker}: ", tag)
                    self._text.insert("end", payload.strip() + "\n")
                    self._text.config(state="disabled")
                    # Only follow the tail if the reader was already there -
                    # yanking the view while someone is scrolling back is rude.
                    if at_bottom:
                        self._text.see("end")
        except queue.Empty:
            pass
        except Exception as e:
            log.debug("live window drain: %s", e)
        if self._root is not None:
            try:
                self._root.after(150, self._drain)
            except Exception:
                pass


if __name__ == "__main__":
    import sys, time
    if "--demo" in sys.argv:
        w = LiveWindow()
        w.start()
        time.sleep(1.0)
        w.set_status("demo - no meeting is being recorded")
        demo = [
            ("Me", "Right, shall we start with the pipeline numbers?"),
            ("Speaker 1", "Yes. We ran it over the weekend and it finished Sunday night."),
            ("Speaker 2", "Roughly how long did the sweep take in the end?"),
            ("Speaker 1", "About eighty hours, but that was with one API key."),
            ("Me", "And with four keys in parallel?"),
            ("Speaker 1", "Call it twenty hours. We have not measured it properly yet."),
        ]
        for who, line in demo:
            time.sleep(1.4)
            w.append(line, speaker=who)
        w.set_status("demo finished - close the window when you are done")
        while w.is_open():
            time.sleep(0.4)
        print("window closed")
