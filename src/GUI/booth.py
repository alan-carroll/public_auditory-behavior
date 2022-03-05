import tasks.utility_funcs  # Must be imported early for twisted reactor
import tkinter as tk
from tkinter import ttk
import cv2
from PIL import Image, ImageTk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from twisted.internet import tksupport, reactor, task
from twisted.internet.defer import inlineCallbacks
import blinker
from functools import partial
from googleapiclient.discovery import build
import numpy as np
import sys, traceback


class Booth(tk.Frame):
    def __init__(self, parent, client, booth_num, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent
        self.parent.geometry("700x500")
        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(0, weight=1)
        self.parent.protocol("WM_DELETE_WINDOW", self.quit)
        self.client = client
        self.booth_num = booth_num
        self.booth_info = client.booth_info[booth_num]
        self.parent.title(f"Booth {self.booth_num}")
        self.frame = ttk.Frame(self.parent)
        self.frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.S, tk.E))

        self.pause_signal = blinker.signal(f"Pause_{self.booth_num}")
        self.pause_signal.connect(self.handle_pause)
        self.is_paused = False

        self.rat_signal = blinker.signal(f"Rat_{self.booth_num}")
        self.rat_signal.connect(self.handle_rat, sender="server")

        self.running_signal = blinker.signal(f"Running_{self.booth_num}")
        self.running_signal.connect(self.handle_running)

        # Webcam
        self.camera_num = self.booth_info["Camera"]
        self.camera_label = ttk.Label(self.frame)
        self.camera_feed = VidCap(self.camera_num, self.camera_label)
        self.camera_feed.feed_loop.start(self.camera_feed.delay)

        # Info / Controls
        self.info_frame = ttk.Frame(self.frame)
        self.control_frame = ttk.Labelframe(self.info_frame, text="Controls")
        self.session_frame = ttk.Labelframe(self.info_frame, text="Session Info")
        self.control_frame.pack(side="left")
        self.session_frame.pack(side="left")
        self.rat_selection = ttk.Combobox(self.control_frame, state="readonly",
                                          values=self.client.rat_list_values + [""])
        self.rat_selection.bind("<<ComboboxSelected>>", partial(self.select_rat))
        self.rat_selection.pack(side="top")
        self.booth_start_button = ttk.Button(self.control_frame, text="Start", command=self.start_session)
        self.booth_start_button.pack(side="top")
        self.booth_stop_button = ttk.Button(self.control_frame, text="Stop/Save", command=self.stop_session, state="disabled")
        self.booth_stop_button.pack(side="top")
        self.booth_pause_button = tk.Button(self.control_frame, text="Pause", state="disabled",
                                            command=self.pause)
        self.booth_pause_button.pack(side="top")
        # TODO add comment functionality
        self.booth_comment_button = ttk.Button(self.control_frame, text="Add comment", state="disabled")
        self.booth_comment_button.pack(side="top")
        self.booth_test_button = ttk.Button(self.control_frame, text="Test", command=self.test_func)
        self.booth_test_button.pack(side="top")

        # Session Info panel
        self.rat_label = ttk.Label(self.session_frame, text="Rat: - ")
        self.rat_label.pack(side="top")
        self.session_status_label = ttk.Label(self.session_frame, text="Status: - ")
        self.session_status_label.pack(side="top")
        self.task_label = ttk.Label(self.session_frame, text="Task: - ")
        self.task_label.pack(side="top")
        self.session_time_label = ttk.Label(self.session_frame, text="Time: - ")
        self.session_time_label.pack(side="top")
        self.pellet_label = ttk.Label(self.session_frame, text="Pellets: - ")
        self.pellet_label.pack(side="top")
        self.trial_number_label = ttk.Label(self.session_frame, text="Trial: - ")
        self.trial_number_label.pack(side="top")
        self.sound_label = ttk.Label(self.session_frame, text="Sound: - ")
        self.sound_label.pack(side="top")
        self.last_active_label = ttk.Label(self.session_frame, text="Last Active: - ")
        self.last_active_label.pack(side="top")

        # Plots
        self.plot_notebook = ttk.Notebook(self.frame)

        # Booth GUI frame geometry
        self.camera_label.grid(row=0, column=0)
        self.info_frame.grid(row=0, column=1)
        self.plot_notebook.grid(row=1, columnspan=2, sticky=(tk.N, tk.S, tk.W, tk.E))

        self.frame.columnconfigure(0, weight=1)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=0)
        self.frame.rowconfigure(1, weight=1)

        self.rat = None
        self.task_id = None
        self.task = None
        self.running = False
        self.state = "Initialized"

    def test_func(self):
        print("Manual sending response")
        self.task.response_signal.send("Manual button")

    @inlineCallbacks
    def start_session(self):
        # TODO add "no task loaded" else clause for info pane logger
        print("Starting session")
        if self.task_id and self.task and not self.running:
            yield self.task.start_session()

    @inlineCallbacks
    def stop_session(self):
        if self.task and self.running:
            yield self.task.stop_session()
            self.task_id = None  # Leave task up until it is reset so plots are still visible

    def select_rat(self, event):
        rat = event.widget.get()
        self.rat_signal.send(self.booth_num, rat=rat)
        self.get_task(rat)

    def get_task(self, rat):
        # TODO find a more general way to handle this gc nonsense. The loops holds references!
        import gc
        if self.task:
            self.task.session_time_loop = None
            self.task.auto_save_loop = None
            self.task.response_loop = None
            self.task.wait_loop = None
            for reference in gc.get_referrers(self.task):
                print(f"\n{reference}\n")
        self.rat = rat
        self.task_id = None
        self.task = None
        if rat:
            task_id = self.client.parameters_info.loc[self.client.parameters_info["Rat"] == rat, "Task"].values[0]
            if isinstance(task_id, str):
                self.task_id = task_id
                try:
                    self.task = tasks.utility_funcs.get_task(self.task_id, self)
                    self.booth_start_button["state"] = "normal"
                    self.task.setup_plots()
                except Exception as e:
                    # TODO make all of this explicit error handling
                    print(f"{self.task_id} not found in utility_funcs.get_task().")
                    print(e)
                    traceback.print_exc(file=sys.stdout)
            else:
                self.task_id = "Not Assigned"
                
        self.rat_label["text"] = f"Rat: {self.rat}"
        self.task_label["text"] = f"Task: {self.task_id}"

    def pause(self):
        self.pause_signal.send(self.booth_num, pause=(not self.is_paused))

    def quit(self):
        # TODO add else that will print to logging widget so user knows they did something wrong
        if not self.running:
            self.camera_feed.feed_loop.stop()
            self.camera_feed = None
            self.parent.destroy()

    def handle_pause(self, _sender, pause):
        self.is_paused = pause
        if self.is_paused:
            self.booth_pause_button.configure(relief="sunken")
        else:
            self.booth_pause_button.configure(relief="raised")

    def handle_rat(self, _sender, rat):
        self.rat_selection.set(rat)
        self.get_task(rat)

    def handle_running(self, _sender, running):
        self.running = running
        if running:
            self.booth_pause_button["state"] = "normal"
            self.booth_stop_button["state"] = "normal"
            self.booth_comment_button["state"] = "normal"
            self.booth_start_button["state"] = "disabled"
            self.rat_selection["state"] = "disabled"
        else:
            self.booth_pause_button["state"] = "disabled"
            self.booth_stop_button["state"] = "disabled"
            self.booth_comment_button["state"] = "disabled"
            self.rat_selection["state"] = "readonly"


class VidCap:
    def __init__(self, cam_num, label):
        self.label = label
        self.delay = 0.030
        self.run = False
        self.feed_loop = task.LoopingCall(self.stream)  # TODO Holds a reference
        self.vid = cv2.VideoCapture(cam_num, cv2.CAP_DSHOW)
        if not self.vid.isOpened():
            raise ValueError("Unable to open video source", cam_num)

        self.vid.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        self.vid.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

    def stream(self):
        if self.vid.isOpened():
            ret, frame = self.vid.read()
            if ret:
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                tk_img = ImageTk.PhotoImage(Image.fromarray(img))
                self.label.config(image=tk_img)
                self.label.image = tk_img

    def __del__(self):
        if self.vid.isOpened():
            self.vid.release()


class BoothPlot:
    def __init__(self, notebook, title="BoothPlot", figsize=(4, 2)):
        self.tab = ttk.Frame(notebook)
        notebook.add(self.tab, text=title)
        self.fig = Figure(figsize=figsize)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.tab)
        self.canvas.draw()


class ShapingPlot(BoothPlot):
    def __init__(self, notebook, title="BoothPlot", figsize=(4, 2)):
        super().__init__(notebook, title=title, figsize=figsize)
        self.plot = self.fig.add_subplot(xlabel="Session Time", ylabel="Pellet Number")
        self.fig.tight_layout()


class ResponsePlot(BoothPlot):
    def __init__(self, notebook, hit_win_start=0.15, hit_win_dur=3, trial_interval=8,
                 title="BoothPlot", figsize=(4, 2)):
        super().__init__(notebook, title=title, figsize=figsize)
        self.response_percent_plot = self.fig.add_subplot(131, xlabel="Sounds", ylabel="% Response")
        self.percent_bars = {}
        self.response_times_plot = self.fig.add_subplot(132, xlabel="Session Time", ylabel="Response Time")
        self.response_times_plot.set_ylim(0, trial_interval)
        self.hit_window_hspan = self.response_times_plot.axhspan(hit_win_start, hit_win_dur + hit_win_start, alpha=0.5)
        self.percent_correct_plot = self.fig.add_subplot(133, xlabel="", ylabel="% Correct")
        self.fig.tight_layout()


class TestClient(tk.Frame):
    def __init__(self, parent, booth_num):
        super().__init__()
        self.parent = parent
        self.booth_info = {booth_num: {"Camera": 0}}
        self.rat_list_values = ["Test1", "Test2", "Test3"]
        self.booth = Booth(self.parent, self, booth_num)

    def quit(self):
        reactor.stop()


if __name__ == "__main__":
    root = tk.Tk()
    tksupport.install(root)
    test_client = TestClient(root, 1)
    root.protocol("WM_DELETE_WINDOW", test_client.quit)
    reactor.run()
