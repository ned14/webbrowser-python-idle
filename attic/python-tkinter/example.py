import sys
import tkinter as tk

window = tk.Tk()
greeting = tk.Label(text="I am working!")
greeting.pack()
window.update()

# Trace marker: emitted immediately before mainloop() begins. The syscall
# trace (strace decodes this write) and the X11-call trace (logger stderr
# stream) are both cut at this line, giving an unambiguous "up to the
# mainloop begins" boundary. The marker file gives a greppable cut point in
# syscall logs that do not decode write() buffers. Keep this file identical
# when re-running under CheerpX so the traces compare 1:1.
print("TRACE_MAINLOOP_BEGIN", file=sys.stderr, flush=True)
with open("/tmp/TRACE_MAINLOOP_BEGIN", "w") as marker:
    marker.write("x")

window.mainloop()
