from tdt import DSPCircuit
from pathlib import Path
import asyncio
from twisted.internet import asyncioreactor

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # Some necessary Windows line
try:
    asyncioreactor.install(asyncio.get_event_loop())
except:
    pass

import tkinter as tk
from tkinter import ttk
from twisted.internet.defer import inlineCallbacks, ensureDeferred, Deferred
from twisted.internet import task, reactor
import blinker
import datetime
import time
import pandas as pd
import numpy as np
from tasks import utility_funcs
import pickle
import json
from functools import partial


class GoNoGoTask:
    def __init__(self, booth):
        self.booth = booth
        self.booth_num = booth.booth_info["Booth"]
        self.upload_info = None
        self.trial_info = None
        self.response_signal = blinker.signal(f"Response_{self.booth_num}")
        self.response_signal.connect(self.handle_response)
        self.pellet_signal = blinker.signal(f"Pellet_{self.booth_num}")
        self.pellet_signal.connect(self.handle_pellet)
        self.pause_signal = blinker.signal(f"Pause_{self.booth_num}")
        self.pause_signal.connect(self.handle_pause)
        self.running_signal = blinker.signal(f"Running_{self.booth_num}")
        self.session_status_signal = blinker.signal(f"Status_{self.booth_num}")
        self.break_event = asyncio.Event()
        self.pause_event = asyncio.Event()
        self.response_buffer = None
        self.response_times = np.array([])
        self.response_poll_delay = 0.1
        self.response_loop = task.LoopingCall(self.get_responses)  # TODO Holds a reference
        self.wait_loop = task.LoopingCall(self.wait_trial)  # TODO Holds a reference
        self.auto_save_time = 60
        self.auto_save_loop = task.LoopingCall(partial(self.save, temp=True))  # TODO Holds a reference
        self.session_time = (0, 0)
        self.session_time_loop = task.LoopingCall(self.update_session_time)  # TODO Holds a reference
        self.save_filepath = None
        self.temp_filename = f"tmp_Booth{self.booth_num}.json"
        self.is_paused = False
        self.awaiting_pause_event = False
        self.pause_deferred = None
        self.pause_start_time = None
        self.total_pause_time = 0
        self.circuit_path = None
        self.circuit = None
        self.session_start_time = None
        self.session_end_time = None
        self.session_run_time = 60
        self.trial_number = 0
        self.trial_start_time = None
        self.trial_interval = 8.0
        self.trial_delay = 0.0
        self.trial_response = None
        self.misses_before_break = 5
        self.misses_in_a_row = 0
        self.misses_break = False
        self.num_pellets = 0
        self.paused = False
        self.in_timeout = False
        self.timeout_length = 6.0
        self.hit_win_start = 150  # ms
        self.hit_win_dur = 3000  # ms
        self.trial_sound = None
        self.cs_plus = []
        self.cs_minus = []
        self.silence = [{"Name": "Silence", "Weight": 0.5}]
        self.plots = {}
        self.session_data = pd.DataFrame()

    def setup_plots(self):
        from GUI.booth import ResponsePlot
        self.plots["Response"] = ResponsePlot(self.booth.plot_notebook, title="Responses", figsize=(4, 2),
                                              hit_win_start=self.hit_win_start / 1000,
                                              hit_win_dur=self.hit_win_dur / 1000,
                                              trial_interval=self.trial_interval)
        self.plots["Response"].canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Initialize bar graphs
        x_pos = 0
        x_ticks = []
        x_labels = []
        for sound in [*self.cs_plus, *self.cs_minus, *self.silence]:
            if sound in self.cs_plus:
                color = "xkcd:green"
            elif sound in self.silence:
                color = "xkcd:red"
            else:
                color = "xkcd:blue"

            x_ticks.append(x_pos)
            x_labels.append(sound["Name"])

            bar = self.plots["Response"].response_percent_plot.bar(x_pos, 0, color=color).patches[0]
            x_pos += 1
            bar.set_label(sound["Name"])
            self.plots["Response"].percent_bars[sound["Name"]] = bar

        self.plots["Response"].response_percent_plot.set_xticks(x_ticks)
        self.plots["Response"].response_percent_plot.set_xticklabels(x_labels, rotation=45)

        # Initialize % Correct
        bar = self.plots["Response"].percent_correct_plot.bar(0, 0, color="xkcd:black").patches[0]
        bar.set_label("% Correct")
        self.plots["Response"].percent_correct_plot.set_xticks([0])
        self.plots["Response"].percent_correct_plot.set_xticklabels(["% Correct"])
        self.plots["Response"].percent_bars["% Correct"] = bar

        self.plots["Response"].response_percent_plot.set_ylim([0, 100])
        self.plots["Response"].percent_correct_plot.set_ylim([0, 100])

        self.plots["Response"].fig.tight_layout()
        self.plots["Response"].canvas.draw()

    def start_session(self):
        print("Got session start message")
        self.circuit.start()
        self.booth.rat_label["text"] = f"Rat: {self.booth.rat}"
        self.booth.task_label["text"] = f"Task: {self.booth.task_id}"
        self.booth.state = "Running"
        self.running_signal.send(self.booth_num, running=True)
        self.session_start_time = datetime.datetime.now()
        self.booth.session_status_label["text"] = f"Status: {self.booth.state}"
        self.auto_save_loop.start(self.auto_save_time, now=False)
        self.prep_trial()
        self.session_time_loop.start(1)
        self.start_trial()

    def stop_session(self):
        self.circuit.stop()
        self.response_loop.stop()
        self.session_time_loop.stop()
        self.auto_save_loop.stop()
        self.booth.state = "Ending"
        self.session_end_time = datetime.datetime.now()
        self.break_event.set()
        self.pause_event.set()

        self.save()
        self.upload()
        self.booth.state = "Stopped"
        self.running_signal.send(self.booth_num, running=False)

    def prep_trial(self):
        pass

    def start_trial(self):
        self.booth.sound_label["text"] = f"Sound: {self.trial_sound['Name']}"
        if self.session_end_time:
            return  # End the cycle!
        self.trial_number += 1
        self.booth.trial_number_label["text"] = f"Trial: {self.trial_number}"
        self.trial_delay = 0.0
        self.trial_start_time = time.time()
        self.response_times = np.array([])
        self.trial_response = None
        self.circuit.trigger(1)  # Make sure to trigger before polling response buffer on first trial
        if not self.response_loop.running:
            self.response_loop.start(self.response_poll_delay)
        self.wait_loop.start(0.1)

    def wait_trial(self):
        if (time.time() - self.trial_start_time) > (self.trial_interval + self.trial_delay):
            self.wait_loop.stop()
            self.end_trial()

    @inlineCallbacks
    def end_trial(self):
        if not self.trial_response:
            if self.trial_sound in self.cs_plus:
                self.trial_response = "Miss"
                self.misses_in_a_row += 1
                if self.misses_before_break <= self.misses_in_a_row:
                    self.misses_in_a_row = 0
                    self.misses_break = True
            else:
                self.trial_response = "Correct rejection"
        self.update_session_data()
        self.update_plots()
        self.update_info()
        yield Deferred.fromFuture(asyncio.ensure_future(self.check_pause()))
        print(f"End trial, trial response: {self.trial_response}")
        self.prep_trial()
        self.start_trial()

    async def check_pause(self):
        if self.misses_break:
            self.booth.state = "On break"
            self.break_event.clear()
            if not self.response_loop.running:
                self.response_loop.start(self.response_poll_delay)
            await self.break_event.wait()
            self.booth.state = "Running"
        elif self.is_paused:
            self.awaiting_pause_event = True
            print("Session is paused")
            # TODO update status label in info pane
            self.booth.state = "Paused"
            self.pause_event.clear()
            await self.pause_event.wait()
            self.awaiting_pause_event = False
            self.booth.state = "Running"

    @inlineCallbacks
    def timeout(self):
        if not self.in_timeout:
            self.circuit.set_tag("light", 0)
            self.in_timeout = True
            self.trial_delay += self.timeout_length
            yield task.deferLater(reactor, self.timeout_length, self.circuit.set_tag, "light", 1)
            self.in_timeout = False

    def get_responses(self):
        self.response_buffer.read_index = 0
        response_times = self.response_buffer.read()[0]  # Returns np array
        if len(response_times) > len(self.response_times):
            self.response_times = response_times
            self.response_signal.send()

    def handle_response(self, _sender):
        if self.awaiting_pause_event:
            return
        # TODO format last active time label
        self.booth.last_active_label["text"] = f"Last Active: {self.session_time[0]}:{self.session_time[1]}"
        self.misses_in_a_row = 0
        if self.misses_break:
            self.misses_break = False
            self.break_event.set()
        elif not self.trial_response:
            if self.hit_win_start < self.response_times[0] < (self.hit_win_start + self.hit_win_dur):
                if self.trial_sound in self.cs_plus:
                    self.trial_response = "Hit"
                    self.pellet_signal.send()
                else:
                    self.trial_response = "False alarm"
                    self.timeout()
            elif self.response_times[0] < self.hit_win_start:
                self.trial_response = "Early"
                self.timeout()
            else:
                self.trial_response = "Late"
                self.timeout()
        elif (self.response_times[-1] - self.response_times[0]) > 1500:  # 1.5 second grace period for multiple pokes
            self.timeout()

    def handle_pellet(self, _sender):
        # Vulintus PD
        # self.circuit.trigger(2)

        # Campden + Arduino PD
        self.booth.client.board.digital[self.booth.booth_info["Pellet Trigger"]].write(1)
        time.sleep(0.01)
        self.booth.client.board.digital[self.booth.booth_info["Pellet Trigger"]].write(0)
        self.num_pellets += 1

    def handle_pause(self, _sender, pause):
        if self.is_paused:
            self.pause_event.set()
        self.is_paused = pause

    def upload(self):
        if self.session_data.empty:
            return
        # TODO Handle nans
        if self.upload_info:
            data = {"values": [
                [
                    datetime.datetime.now().strftime("%Y-%m-%d"),  # date
                    0,  # weight
                    0,  # maxweight
                    0,  # percentweight
                    "AC",  # initials
                    0,  # session num
                    self.booth_num,  # booth num
                    self.session_start_time.strftime("%H:%M"),  # start time
                    self.session_end_time.strftime("%H:%M"),  # end time
                    f"{self.session_time[0]}:{self.session_time[1]}",  # session duration
                    self.num_pellets,  # pellets
                    self.session_data.tail(1)["% Correct"].values[0],  # percent correct
                    self.booth.task_id,  # program name
                    self.booth.task_id,  # task number
                    0,  # soundfile eg. [1, 1, 2, 2]
                    0,  # VNS stims
                    0,  # max impedance
                    0,  # self.session_data.tail(1)["Silence d'"].values[0],  # d' silence
                    0,  # self.session_data.tail(1)["CS- d'"].values[0],  # d' CS-
                    0,  # notes
                    0,  # z filename
                    0,  # c filename
                ],
            ]}
            # Try reading/updating pellets 3 times. After 3 failures, print a message but don't crash
            # TODO integrate this with GUI interface so user gets more obvious notification
            for attempt in range(3):
                try:
                    self.booth.client._sheets.values().append(spreadsheetId=self.upload_info["ID"],
                                                              range=f"{self.booth.rat}_data!A:Z",
                                                              body=data, valueInputOption="USER_ENTERED").execute()
                    pellet_sheet = self.booth.client._sheets.values().get(spreadsheetId=self.upload_info["ID"],
                                                                          range="Weights!A:Z",).execute()['values']
                    pellet_df = pd.DataFrame(pellet_sheet[1:], columns=pellet_sheet[0])
                    pellet_df["Total Pellets"] = pellet_df["Total Pellets"].astype(int)
                    row_idx = pellet_df.loc[pellet_df["Rat"] == self.booth.rat].index[0]
                    total_pellets = pellet_df.iloc[row_idx]["Total Pellets"] + self.num_pellets

                    # Add 2 to row_idx to offset 0-based indexing and a row for the column headers
                    self.booth.client._sheets.values().update(spreadsheetId=self.upload_info["ID"],
                                                              range=f"Weights!E{row_idx + 2}",
                                                              body={"values": [[int(total_pellets)]]},
                                                              valueInputOption="USER_ENTERED"
                                                              ).execute()
                    break
                except:  # TODO provide actual error
                    print(f"Failed to update pellet sheet: Attempt {attempt+1}")
            else:
                print("Failed to update pellet sheet after 3 attempts. Update pellet count manually.")

    def save(self, temp=False, filepath=None, filename=None):
        if self.session_data.empty:
            return
        data = self.session_data.to_json()
        data_dict = {
            "Data": data,
            "Rat": self.booth.rat,
            "Time": datetime.datetime.now().isoformat(),
            # TODO use datetime.datetime.fromisoformat() when reading it in
            "Task": self.booth.task_id,
            "Session Start": self.session_start_time.isoformat(),
            "Session Time": self.session_data.tail(1)["Session Time"].values[0],
            "Pellets": self.num_pellets,
            "% Correct": self.session_data.tail(1)["% Correct"].values[0],
            "Finished": not temp,
        }

        if not filepath:
            if self.save_filepath:
                filepath = self.save_filepath
            else:
                filepath = Path(__file__).parent / "../../data/"
        filepath = Path(filepath)

        if not filename:
            if temp:
                filename = self.temp_filename
            else:
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                session_num = 1
                filename = f"{self.booth.rat}_{today}_Session-{session_num}.json"
                while Path.is_file(filepath / filename):
                    session_num += 1
                    filename = f"{self.booth.rat}_{today}_Session-{session_num}.json"

        filepath.mkdir(parents=True, exist_ok=True)
        with (filepath / filename).open(mode="w") as file:
            json.dump(data_dict, file)

    def update_session_data(self):
        if self.session_data.empty:  # Handle first trial special to set up columns and values
            data_dict = {}
            for sound in ["CS+", "CS-", *self.cs_plus, *self.cs_minus, *self.silence]:
                if isinstance(sound, dict):
                    sound_name = sound["Name"]
                else:
                    sound_name = sound
                data_dict[f"{sound_name} Hits"] = 0
                data_dict[f"{sound_name} Trials"] = 0
                data_dict[f"{sound_name} % Hit"] = np.nan
                if sound not in ["CS+", *self.cs_plus]:
                    data_dict[f"{sound_name} d'"] = np.nan
        else:  # Copy values from last trial as starting point -- many analysis columns won't change
            data_dict = self.session_data.tail(1).to_dict("records")[0]

        # Get Sound Category (CS+, CS-, Silence)
        if self.trial_sound in self.cs_plus:
            sound_category = "CS+"
        elif self.trial_sound in self.cs_minus:
            sound_category = "CS-"
        else:
            sound_category = "Silence"

        # Get 'Hit' or not -- 'Hit' if response falls within hit window, no matter the sound category
        if self.trial_response in ["Hit", "False alarm"]:
            hit = 1
        else:
            hit = 0

        # Fill out data dict with trial info
        data_dict["Trial Num"] = self.trial_number
        minute, second = self.session_time
        data_dict["Session Time"] = {"Minute": minute, "Second": second}
        data_dict["Sound"] = self.trial_sound
        data_dict["Sound Category"] = sound_category
        data_dict["Response Times"] = self.response_times
        data_dict["Response"] = self.trial_response
        data_dict["Hit"] = hit

        data_dict[f"{sound_category} Hits"] += hit
        data_dict[f"{sound_category} Trials"] += 1
        data_dict[f"{sound_category} % Hit"] = \
            data_dict[f"{sound_category} Hits"] / data_dict[f"{sound_category} Trials"] * 100.0

        sound_name = self.trial_sound["Name"]
        data_dict[f"{sound_name} Hits"] += hit
        data_dict[f"{sound_name} Trials"] += 1
        data_dict[f"{sound_name} % Hit"] = data_dict[f"{sound_name} Hits"] / data_dict[f"{sound_name} Trials"] * 100.0

        # Calculate d'
        for sound in ["CS-", *self.cs_minus, *self.silence]:
            if isinstance(sound, dict):
                sound_name = sound["Name"]
            else:
                sound_name = sound
            data_dict[f"{sound_name} d'"] = utility_funcs.calc_d_prime(
                hits=data_dict["CS+ Hits"], misses=data_dict["CS+ Trials"] - data_dict["CS+ Hits"],
                false_alarms=data_dict[f"{sound_name} Hits"],
                correct_rejections=data_dict[f"{sound_name} Trials"] - data_dict[f"{sound_name} Hits"]
            )

        if self.cs_minus:
            data_dict["% Correct"] = np.nanmean([data_dict["CS+ % Hit"], (100 - data_dict["CS- % Hit"])])
        else:
            data_dict["% Correct"] = np.nanmean([data_dict["CS+ % Hit"], (100 - data_dict["Silence % Hit"])])

        self.session_data = self.session_data.append(data_dict, ignore_index=True)

    def update_plots(self):
        # Plot trial response
        session_time = (datetime.datetime.now() - self.session_start_time).seconds / 60
        if self.response_times.any():
            response_time = self.response_times[0] / 1000
        else:
            response_time = 0
        if self.trial_sound in self.cs_plus:
            response_color = "xkcd:green"
        elif self.trial_sound in self.cs_minus:
            response_color = "xkcd:blue"
        else:
            response_color = "xkcd:red"
        # TODO Make it look nice, yea? X markers for non-response, nice colors etc.
        self.plots["Response"].response_times_plot.plot(session_time, response_time, "o",
                                                        color=response_color, ms=3)

        # Find matching % Response bar and update height
        name = self.trial_sound["Name"]
        self.plots["Response"].percent_bars[name].set_height(self.session_data.tail(1)[f"{name} % Hit"].values[0])

        # TODO Update percent correct plot
        self.plots["Response"].percent_bars["% Correct"].set_height(self.session_data.tail(1)["% Correct"].values[0])

        # Keep response times ylim matched to the trial interval in the case of tasks that dynamically change it
        self.plots["Response"].response_times_plot.set_ylim([0, self.trial_interval])

        # Same for hit window patch; update the polygon vertices
        xy = self.plots["Response"].hit_window_hspan.get_xy()
        xy[1, 1] = xy[2, 1] = (self.hit_win_dur + self.hit_win_start) / 1000

        self.plots["Response"].fig.tight_layout()
        self.plots["Response"].canvas.draw()

    def update_info(self):
        # Update booth session info panel
        self.booth.session_status_label["text"] = f"Status: {self.booth.state}"
        self.booth.pellet_label["text"] = f"Pellets: {self.num_pellets}"

        status_dict = {
            "Status": self.booth.state,
            "Time": self.session_time,
            "Trial": self.trial_number,
            "Pellet": self.num_pellets,
            "Sound": self.trial_sound["Name"],
            "Percent": self.session_data.tail(1)["% Correct"].values[0],
            "Attempt": "-",
            "Session": "-",
        }
        self.session_status_signal.send(self.booth_num, status_dict=status_dict)

    def update_session_time(self):
        self.session_time = divmod((datetime.datetime.now() - self.session_start_time).seconds, 60)
        self.booth.session_time_label["text"] = f"Time: {self.session_time[0]}:{self.session_time[1]}"

    def __del__(self):
        print("\n\n\nDeleting task!\n\n\n")
        if self.circuit:
            self.circuit.stop()
        for plot in self.plots.values():
            plot.tab.destroy()
