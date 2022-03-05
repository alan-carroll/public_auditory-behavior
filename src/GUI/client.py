import tkinter as tk
import os
from twisted.internet import tksupport, reactor
from autobahn.twisted.component import Component
from twisted.internet.defer import inlineCallbacks
import pickle
from pathlib import Path
from googleapiclient.discovery import build
import pandas as pd
from GUI.booth import Booth
from tdt import dsp_server
import multiprocessing as mp
from functools import partial
import blinker
import numpy as np
from pyfirmata import Arduino, util
import serial


def run_tdt_rpc(address=("localhost", 3333), interface="USB"):
    print("Started TDTPy RPC Server in the background ... ")
    dsp_server.TDTRPCServer(address=address, interface=interface).run_forever()


class Client(tk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.computer = os.environ['COMPUTERNAME']

        # Start TDTPy RPC server as a background process
        self.tdt_rpc_address = ("localhost", 3333)
        self.tdt_rpc_p = mp.Process(target=run_tdt_rpc,
                                    kwargs={"address": self.tdt_rpc_address, "interface": "USB"})
        self.tdt_rpc_p.start()
        print(f"TDTPy RPC Server PID: {self.tdt_rpc_p.pid}")

        # Initialize Google sheets object
        path = Path(__file__).parent / "../../resources/credentials/token.pickle"
        with path.open("rb") as token:
            sheets_creds = pickle.load(token)
        sheets_service = build('sheets', 'v4', credentials=sheets_creds)
        self._sheets = sheets_service.spreadsheets()

        # Connect to Crossbar.io WAMP router hosted on the Server computer
        server_info = pd.read_csv(Path(__file__).parent / "../../resources/credentials/server_info.csv")
        host, port, realm = server_info.iloc[0].values
        self.router_session = None
        self.component = Component(transports=f"ws://{host}:{port}", realm=realm)
        self.component.on_join(self.__joined)
        self.component.on_leave(self.__left)
        self.component.start(reactor)

        # Get misc parameters for this computer (RP2 / Camera / Booth numbers + whatever else)
        df = pd.read_csv(Path(__file__).parent / "../../resources/credentials/sheet_ids.csv")
        self.sheets_ids = {val.Name: val.ID for idx, val in df.iterrows()}
        comp_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                              range="Computers!A:Z").execute()['values']
        comp_df = pd.DataFrame(comp_info[1:], columns=comp_info[0])
        comp_df["Booth"] = comp_df["Booth"].astype(int)
        comp_df["Camera"] = comp_df["Camera"].astype(int)
        comp_df["RP2"] = comp_df["RP2"].astype(int)
        comp_df["Pellet Trigger"] = comp_df["Pellet Trigger"].astype(int)
        comp_df["Pellet Error"] = comp_df["Pellet Error"].astype(int)
        comp_df["Pellet Status"] = comp_df["Pellet Status"].astype(int)

        self.comp_info = comp_df[comp_df["Computer"] == self.computer]
        self.booth_info = {row["Booth"]: row for row in self.comp_info.to_dict("records")}

        # Arduino -- should be on COM3 or 4, but check a few extra anyway
        self.board = None
        for com_port in range(3, 8):
            try:
                self.board = Arduino(f"COM{com_port}")
                print(f"Opened Arduino on COM{com_port}!")
                break
            except serial.SerialException:
                print(f"Failed to open Arduino on COM{com_port}")
        else:
            print("Couldn't open Arduino on any of the checked ports.")
            print("Check USB is plugged in and/or check Windows Device Manager COMs.")
            return

        self.arduino_iter = util.Iterator(self.board)
        self.arduino_iter.start()
        self.board.analog[0].enable_reporting()
        self.board.analog[1].enable_reporting()
        self.board.analog[2].enable_reporting()
        self.board.analog[3].enable_reporting()

        # Get parameters for active rats
        parameters_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                                    range="Parameters!A:Z").execute()['values']
        self.parameters_info = pd.DataFrame(parameters_info[1:], columns=parameters_info[0])
        self.parameters_info.replace("nan", np.nan, inplace=True)
        self.rat_list_values = self.parameters_info["Rat"].values.tolist()

        # Initialize GUI components
        self.parent = parent
        self.parent.geometry("400x300")
        self.frame = tk.Frame(self.parent, background="red")
        self.parent.title(f"Client {self.computer}")

        # Refresh parameters
        self.refresh_button = tk.Button(self.frame, text="Refresh parameters", command=self.refresh_parameters)
        self.refresh_button.pack()

        # Test functions
        self.test_button = tk.Button(self.frame, text="Test Func", command=partial(self.test_func, 1))
        self.test_button.pack()

        # Booth buttons and blinker signals
        self.booth_buttons = {}
        self.pause_signals = {}
        self.rat_signals = {}
        self.running_signals = {}
        self.session_status_signals = {}
        for booth in self.booth_info.keys():
            self.booth_buttons[booth] = tk.Button(self.frame, text=f"Open booth {booth}",
                                                  command=partial(self.open_booth, booth))
            self.booth_buttons[booth].pack()
            self.pause_signals[booth] = blinker.signal(f"Pause_{booth}")
            self.pause_signals[booth].connect(self.handle_pause)
            self.rat_signals[booth] = blinker.signal(f"Rat_{booth}")
            self.rat_signals[booth].connect(self.handle_rat)
            self.running_signals[booth] = blinker.signal(f"Running_{booth}")
            self.running_signals[booth].connect(self.handle_running)
            self.session_status_signals[booth] = blinker.signal(f"Status_{booth}")
            self.session_status_signals[booth].connect(self.handle_session_status)

        self.quit_button = tk.Button(self.frame, text='Quit!', command=self.quit)
        self.quit_button.pack()
        self.booths = {}
        self.booth_objs = {}
        self.frame.pack(fill="both")

    @inlineCallbacks
    def __joined(self, session, _details):
        print("Client joined session!")
        self.router_session = session
        yield self.router_session.subscribe(self.open_booth, "client.open_booth")
        yield self.router_session.subscribe(self.close_booth, "client.close_booth")
        yield self.router_session.subscribe(self.start_booth, "client.start_booth")
        yield self.router_session.subscribe(self.stop_booth, "client.stop_booth")
        yield self.router_session.subscribe(self.pause_booth, "client.pause_booth")
        yield self.router_session.subscribe(self.add_comment, "client.add_comment")
        yield self.router_session.subscribe(self.select_rat, "client.select_rat")
        yield self.router_session.subscribe(self.refresh_booth, "client.refresh_booth")

        yield self.router_session.subscribe(self.test_func, "test.test_func")
        yield self.router_session.subscribe(self.test_func2, "test.test_func2")

    def refresh_parameters(self):
        parameters_info = self._sheets.values().get(spreadsheetId=self.sheets_ids["ATAT Behavior"],
                                                    range="Parameters!A:Z").execute()['values']
        self.parameters_info = pd.DataFrame(parameters_info[1:], columns=parameters_info[0])
        self.parameters_info.replace("nan", np.nan, inplace=True)
        self.rat_list_values = self.parameters_info["Rat"].values.tolist()
        for booth in self.booth_objs.values():
            booth.rat_selection["values"] = self.rat_list_values + [""]

    def test_func(self, booth_num):
        self.running_signals[booth_num].send(booth_num, running=True)

    @staticmethod
    def __left(_details, _was_clean):
        print("Client left session!")

    def booth_exists(self, booth_num):
        return booth_num in self.booths and self.booths[booth_num].winfo_exists()

    def refresh_booth(self, booth_num):
        # TODO Grab booth info
        booth_info = None
        self.router_session.publish("server.refresh_booth", booth_info)

    def open_booth(self, booth_num):
        if booth_num in self.booth_info:
            if self.booth_exists(booth_num) and self.booths[booth_num].state() == "normal":
                self.booths[booth_num].focus()
            else:
                self.booths[booth_num] = tk.Toplevel(self.parent)
                self.booth_objs[booth_num] = Booth(self.booths[booth_num], self, booth_num)
                self.booths[booth_num].focus()

    def close_booth(self, booth_num):
        if booth_num in self.booth_info and self.booth_exists(booth_num):
            self.booth_objs[booth_num].quit()

    def start_booth(self, booth_num):
        if booth_num in self.booth_info and self.booth_exists(booth_num):
            self.booth_objs[booth_num].start_session()

    def stop_booth(self, booth_num):
        if booth_num in self.booth_info and self.booth_exists(booth_num):
            self.booth_objs[booth_num].stop_session()

    def pause_booth(self, booth_num, pause):
        if booth_num in self.booth_info and self.booth_exists(booth_num):
            self.pause_signals[booth_num].send(booth_num, pause=pause)

    def handle_pause(self, sender, pause):
        self.router_session.publish("server.pause_booth", sender, pause)

    def handle_rat(self, sender, rat):
        self.router_session.publish("server.select_rat", sender, rat)

    def handle_running(self, sender, running):
        self.router_session.publish("server.running_status", sender, running)

    def handle_session_status(self, _sender, status_dict):
        self.router_session.publish("server.session_status", status_dict)

    def add_comment(self, booth_num):
        # TODO
        if booth_num in self.booth_info:
            pass

    def select_rat(self, booth_num, rat):
        if booth_num in self.booth_info and self.booth_exists(booth_num):
            self.rat_signals[booth_num].send("server", rat=rat)

    def quit(self):
        for obj in self.booth_objs.values():
            if obj.running:
                return
            # TODO if booth GUI closed manually already it complains. Wrap in try?
            obj.quit()
        self.tdt_rpc_p.terminate()
        reactor.stop()


def create_gui():
    root = tk.Tk()
    tksupport.install(root)
    client = Client(root)
    root.protocol("WM_DELETE_WINDOW", client.quit)
    return client


if __name__ == "__main__":
    client_gui = create_gui()
    reactor.run()
