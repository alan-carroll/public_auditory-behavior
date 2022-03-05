import tkinter as tk
from tkinter import ttk
import os
from twisted.internet import tksupport, reactor
from autobahn.twisted.component import Component
from twisted.internet.defer import inlineCallbacks
import pickle
from pathlib import Path
from googleapiclient.discovery import build
import pandas as pd
from itertools import zip_longest
from functools import partial
from datetime import datetime

# TODO Server GUI rows are too long. Only extends to 'Add ' comment on vertical monitor
# TODO If booth closes, clear server's rat selection


class Server(tk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.computer = os.environ["COMPUTERNAME"]
        self.parent = parent
        self.parent.geometry("400x300")
        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(0, weight=1)
        self.frame = ttk.Frame(self.parent, padding=[3, 3, 3, 3])
        self.frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.S))
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.parent.title(f"Server {self.computer}")

        path = Path(__file__).parent / "../../resources/credentials/token.pickle"
        with path.open("rb") as token:
            sheets_creds = pickle.load(token)
        sheets_service = build('sheets', 'v4', credentials=sheets_creds)
        self._sheets = sheets_service.spreadsheets()

        server_info = pd.read_csv(Path(__file__).parent / "../../resources/credentials/server_info.csv")
        host, port, realm = server_info.iloc[0].values
        self.router_session = None
        self.component = Component(transports=f"ws://{host}:{port}", realm=realm)
        self.component.on_join(self.__joined)
        self.component.on_leave(self.__left)
        self.component.start(reactor)

        df = pd.read_csv(Path(__file__).parent / "../../resources/credentials/sheet_ids.csv")
        self.sheets_ids = {val.Name: val.ID for idx, val in df.iterrows()}

        # Get session running orders
        sess_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                              range="Sessions!A:Z", majorDimension="COLUMNS").execute()['values']
        session_dicts = []
        for session in range(1, len(sess_info)):
            rat_list = [rat if rat else None for rat in sess_info[session][1:]]
            session_dicts.extend([{"Booth": int(booth), "Session": int(session), "Rat": rat} for booth, rat in
                                  zip_longest(sess_info[0][1:], rat_list)])
        self.sessions = pd.DataFrame(session_dicts)

        # Get parameters for active rats
        parameters_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                                    range="Parameters!A:Z").execute()['values']
        self.parameters_info = pd.DataFrame(parameters_info[1:], columns=parameters_info[0])
        self.rat_list_values = self.parameters_info["Rat"].values.tolist()

        # Super control widgets
        self.control_frame = ttk.LabelFrame(self.frame, text="Controls", padding=[1, 1, 1, 1])
        self.booth_control_frame = ttk.LabelFrame(self.frame, text="Booths", padding=[1, 1, 1, 1])
        self.control_frame.pack()
        self.booth_control_frame.pack()
        self.refresh_parameters_button = ttk.Button(self.control_frame, text="Refresh parameters",
                                                    command=self.refresh_parameters)
        self.start_all_button = ttk.Button(self.control_frame, text="Start All", command=self.start_all_booths)
        self.stop_all_button = ttk.Button(self.control_frame, text="Stop and Save All", command=self.stop_all_booths)
        self.session_label = ttk.Label(self.control_frame, text="Select Session: ")
        self.session_selection = ttk.Combobox(self.control_frame, state="readonly",
                                              values=self.sessions["Session"].unique().tolist())
        self.session_selection.bind("<<ComboboxSelected>>", self.select_session)
        self.refresh_parameters_button.pack(side="left")
        self.start_all_button.pack(side="left")
        self.stop_all_button.pack(side="left")
        self.session_label.pack(side="left")
        self.session_selection.pack(side="left")

        # Iterate booth widgets
        self.booth_frames = {}
        self.booth_labels = {}
        self.booth_refresh_buttons = {}
        self.booth_open_buttons = {}
        self.booth_close_buttons = {}
        self.booth_rat_selecions = {}
        self.booth_status_labels = {}
        self.booth_time_labels = {}
        self.booth_trial_labels = {}
        self.booth_pellet_labels = {}
        self.booth_sound_labels = {}
        self.booth_percent_labels = {}
        self.booth_daily_labels = {}
        self.booth_attempt_labels = {}
        self.booth_start_buttons = {}
        self.booth_stop_buttons = {}
        self.booth_pause_buttons = {}
        self.booth_comment_buttons = {}
        self.booth_last_activity_labels = {}
        self.booth_test_func_buttons = {}
        self.booth_weight_labels = {}  # TODO
        for idx, booth in enumerate(self.sessions["Booth"].unique()):
            booth = int(booth)
            self.booth_frames[booth] = ttk.Frame(self.booth_control_frame, padding=[1, 1, 1, 1])
            self.booth_frames[booth].pack(fill="y", expand=True)
            self.booth_labels[booth] = ttk.Label(self.booth_frames[booth], text=f"Booth {booth}")
            self.booth_refresh_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Refresh",
                                                           command=partial(self.refresh_booth, booth))
            self.booth_open_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Open",
                                                        command=partial(self.open_booth, booth))
            self.booth_close_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Close",
                                                         command=partial(self.close_booth, booth))
            self.booth_rat_selecions[booth] = ttk.Combobox(self.booth_frames[booth], justify="right", state="readonly",
                                                           values=self.rat_list_values + [""])
            self.booth_rat_selecions[booth].bind("<<ComboboxSelected>>", partial(self.select_rat, booth))
            self.booth_status_labels[booth] = ttk.Label(self.booth_frames[booth], text="Disconnected")
            self.booth_time_labels[booth] = ttk.Label(self.booth_frames[booth], text="--:--")
            self.booth_trial_labels[booth] = ttk.Label(self.booth_frames[booth], text="Trial: -")
            self.booth_pellet_labels[booth] = ttk.Label(self.booth_frames[booth], text="Pellets: -")
            self.booth_sound_labels[booth] = ttk.Label(self.booth_frames[booth], text="Sound:")
            self.booth_percent_labels[booth] = ttk.Label(self.booth_frames[booth], text="-% correct")
            self.booth_daily_labels[booth] = ttk.Label(self.booth_frames[booth], text="Session: -")
            self.booth_attempt_labels[booth] = ttk.Label(self.booth_frames[booth], text="Attempt: -")
            self.booth_start_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Start",
                                                         command=partial(self.start_booth, booth))
            self.booth_stop_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Stop/Save", state="disabled",
                                                        command=partial(self.stop_booth, booth))
            self.booth_pause_buttons[booth] = tk.Button(self.booth_frames[booth], text="Pause", state="disabled",
                                                        command=partial(self.pause_booth, booth))
            self.booth_comment_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Add comment",
                                                           state="disabled", command=partial(self.add_comment, booth))
            self.booth_last_activity_labels[booth] = ttk.Label(self.booth_frames[booth], text="Last update: --:--")
            self.booth_test_func_buttons[booth] = ttk.Button(self.booth_frames[booth], text="Test func",
                                                             command=partial(self.test_func, booth))
            self.booth_labels[booth].pack(side="left")
            self.booth_refresh_buttons[booth].pack(side="left")
            self.booth_open_buttons[booth].pack(side="left")
            self.booth_close_buttons[booth].pack(side="left")
            self.booth_rat_selecions[booth].pack(side="left")
            self.booth_status_labels[booth].pack(side="left")
            self.booth_time_labels[booth].pack(side="left")
            self.booth_trial_labels[booth].pack(side="left")
            self.booth_pellet_labels[booth].pack(side="left")
            self.booth_sound_labels[booth].pack(side="left")
            self.booth_percent_labels[booth].pack(side="left")
            self.booth_daily_labels[booth].pack(side="left")
            self.booth_attempt_labels[booth].pack(side="left")
            self.booth_start_buttons[booth].pack(side="left")
            self.booth_stop_buttons[booth].pack(side="left")
            self.booth_pause_buttons[booth].pack(side="left")
            self.booth_comment_buttons[booth].pack(side="left")
            self.booth_last_activity_labels[booth].pack(side="left")
            self.booth_test_func_buttons[booth].pack(side="left")

    @inlineCallbacks
    def __joined(self, session, _details):
        print("Server joined session!")
        self.router_session = session
        yield self.router_session.subscribe(self.session_status, "server.session_status")
        yield self.router_session.subscribe(self.update_pause, "server.pause_booth")
        yield self.router_session.subscribe(self.update_rat, "server.select_rat")
        yield self.router_session.subscribe(self.update_booth, "server.refresh_booth")
        yield self.router_session.subscribe(self.update_running, "server.running_status")

    @inlineCallbacks
    def __left(self, _details, _was_clean):
        print("Server left session!")

    def test_func(self, booth_num):
        self.router_session.publish("test.test_func", booth_num)

    def refresh_parameters(self):
        # Update session running orders
        sess_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                              range="Sessions!A:Z", majorDimension="COLUMNS").execute()['values']
        session_dicts = []
        for session in range(1, len(sess_info)):
            rat_list = [rat if rat else None for rat in sess_info[session][1:]]
            session_dicts.extend([{"Booth": int(booth), "Session": int(session), "Rat": rat} for booth, rat in
                                  zip_longest(sess_info[0][1:], rat_list)])
        self.sessions = pd.DataFrame(session_dicts)
        self.session_selection["values"] = self.sessions["Session"].unique().tolist()

        # Update rat parameters
        parameters_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                                    range="Parameters!A:Z").execute()['values']
        self.parameters_info = pd.DataFrame(parameters_info[1:], columns=parameters_info[0])
        self.rat_list_values = self.parameters_info["Rat"].values.tolist()
        for booth in self.booth_rat_selecions.values():
            booth["values"] = self.rat_list_values + [""]

    def select_session(self, event):
        session_num = int(event.widget.get())
        session = self.sessions[self.sessions["Session"] == session_num]
        for idx, row in session.iterrows():
            if row["Rat"]:
                self.booth_rat_selecions[row["Booth"]].set(row["Rat"])
                self.router_session.publish("client.select_rat", row["Booth"], row["Rat"])
            else:
                self.booth_rat_selecions[row["Booth"]].set("")
                self.router_session.publish("client.select_rat", row["Booth"], "")

    def open_booth(self, booth_num):
        self.router_session.publish("client.open_booth", booth_num)

    def refresh_booth(self, booth_num):
        self.router_session.publish("client.refresh_booth", booth_num)

    def close_booth(self, booth_num):
        self.router_session.publish("client.close_booth", booth_num)

    def start_booth(self, booth_num):
        self.router_session.publish("client.start_booth", booth_num)

    def start_all_booths(self):
        for booth_num, booth_obj in self.booth_rat_selecions.items():
            if booth_obj["state"] == "normal" and booth_obj.get():
                self.start_booth(booth_num)

    def stop_booth(self, booth_num):
        self.router_session.publish("client.stop_booth", booth_num)

    def stop_all_booths(self):
        for booth_num, booth_obj in self.booth_stop_buttons.items():
            if booth_obj["state"] == "normal":
                self.stop_booth(booth_num)

    def pause_booth(self, booth_num):
        if self.booth_status_labels[booth_num]["text"] == "Paused":
            self.router_session.publish("client.pause_booth", booth_num, False)
        else:
            self.router_session.publish("client.pause_booth", booth_num, True)

    def update_pause(self, booth_num, pause):
        if pause:
            self.booth_pause_buttons[booth_num].configure(relief="sunken")
            self.booth_status_labels[booth_num].configure(text="Paused")
        else:
            self.booth_pause_buttons[booth_num].configure(relief="raised")
            self.booth_status_labels[booth_num].configure(text="Other")  # TODO fix this

    def update_rat(self, booth_num, rat):
        self.booth_start_buttons[booth_num]["state"] = "normal"
        self.booth_rat_selecions[booth_num].set(rat)

    def update_running(self, booth_num, running):
        if running:
            self.booth_pause_buttons[booth_num]["state"] = "normal"
            self.booth_stop_buttons[booth_num]["state"] = "normal"
            self.booth_comment_buttons[booth_num]["state"] = "normal"
            self.booth_start_buttons[booth_num]["state"] = "disabled"
            self.booth_close_buttons[booth_num]["state"] = "disabled"
            self.booth_rat_selecions[booth_num]["state"] = "disabled"
        else:
            self.booth_pause_buttons[booth_num]["state"] = "disabled"
            self.booth_stop_buttons[booth_num]["state"] = "disabled"
            self.booth_comment_buttons[booth_num]["state"] = "disabled"
            self.booth_close_buttons[booth_num]["state"] = "normal"
            self.booth_rat_selecions[booth_num]["state"] = "readonly"

    def update_booth(self, booth_info):
        # TODO
        booth_num = booth_info["Number"]

    def add_comment(self, booth_num):
        self.router_session.publish("client.add_comment", booth_num)

    def select_rat(self, booth_num, event):
        self.booth_start_buttons[booth_num]["state"] = "normal"
        self.router_session.publish("client.select_rat", booth_num, event.widget.get())

    def session_status(self, status_dict, booth_num):
        self.booth_status_labels[booth_num].configure(text=status_dict["Status"])
        self.booth_time_labels[booth_num].configure(text=f"{status_dict['Time'][0]}:{status_dict['Time'][1]}")
        self.booth_trial_labels[booth_num].configure(text=f"Trial: {status_dict['Trial']}")
        self.booth_pellet_labels[booth_num].configure(text=f"Pellet: {status_dict['Pellet']}")
        self.booth_sound_labels[booth_num].configure(text=f"Sound: {status_dict['Sound']}")
        self.booth_percent_labels[booth_num].configure(text=f"{status_dict['Percent']:.1f}% correct")
        self.booth_attempt_labels[booth_num].configure(text=f"Attempt: {status_dict['Attempt']}")
        self.booth_daily_labels[booth_num].configure(text=f"Session: {status_dict['Session']}")
        self.update_last_active(booth_num)

    def update_last_active(self, booth_num):
        last_active = datetime.now().strftime("%H:%M")
        self.booth_last_activity_labels[booth_num].configure(text=f"Last active: {last_active}")

    def quit(self):
        reactor.stop()


def create_gui():
    root = tk.Tk()
    tksupport.install(root)
    server = Server(root)
    root.protocol("WM_DELETE_WINDOW", server.quit)
    return server


if __name__ == "__main__":
    server_gui = create_gui()
    reactor.run()
