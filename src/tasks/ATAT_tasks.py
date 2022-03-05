import pickle
from tdt import DSPCircuit
from pathlib import Path
from tasks import task, utility_funcs, PsiMarginal
import pandas as pd
import numpy as np
import time
import tkinter as tk
from psychopy.data import QuestPlusHandler
import datetime
from scipy.io import wavfile


class ToneShapingTask(task.GoNoGoTask):
    def __init__(self, booth):
        super().__init__(booth)
        self.circuit_path = Path(__file__).parent / "../../resources/circuits/ATAT_Tone_shape.rcx"
        self.circuit = DSPCircuit(self.circuit_path, "RP2", device_id=self.booth.booth_info["RP2"],
                                  interface="USB", address=self.booth.client.tdt_rpc_address)
        print("Loaded circuit!")
        self.num_responses = 0

        if self.booth_num < 10:
            booth_str = f"0{self.booth_num}"
        else:
            booth_str = f"{self.booth_num}"
        calibrations_path = Path(__file__).parent / f"../../resources/tasks/ATAT_B{booth_str}_speaker_amps.csv"
        self.tone_calibrations = pd.read_csv(calibrations_path)

        self.shaping_freq = 2000
        self.shaping_int = 60
        amp = self.tone_calibrations.loc[(self.tone_calibrations["Freq"] == self.shaping_freq) &
                                         (self.tone_calibrations["Int"] == self.shaping_int), "Amp"].values[0]
        self.circuit.set_tags(tone_freq=self.shaping_freq, tone_amp=amp, )  # light=1)

        self.hit_win_dur = np.inf
        self.trial_sound = {"Name": f"{self.shaping_freq} Hz", "Weight": 1.0,
                            "Freq": self.shaping_freq, "Int": self.shaping_int}
        self.cs_plus = [self.trial_sound]

        self.save_filepath = Path(__file__).parent / f"../../data/ATAT/{self.booth.task_id}/"
        self.upload_info = {"ID": self.booth.client.sheets_ids["ATAT Behavior"]}

    def setup_plots(self):
        from GUI.booth import ShapingPlot
        self.plots["Shaping"] = ShapingPlot(self.booth.plot_notebook, title="Shaping", figsize=(2, 1))
        self.plots["Shaping"].canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def start_trial(self):
        self.booth.sound_label["text"] = f"Sound: {self.trial_sound['Name']}"
        if self.session_end_time:
            return  # End the cycle!
        self.trial_number += 1
        self.booth.trial_number_label["text"] = f"Trial: {self.trial_number}"
        self.trial_start_time = time.time()
        self.response_times = []
        self.trial_response = None
        if not self.response_loop.running:
            self.response_loop.start(self.response_poll_delay)

    def get_responses(self):
        # Have to handle different response circuit in shaping
        num_responses = self.circuit.get_tag("ltimes")
        if num_responses > self.num_responses:
            self.num_responses = num_responses
            print("Sending message")
            self.response_signal.send()

    def handle_response(self, _sender):
        if self.awaiting_pause_event:
            return
        # Have to handle different response circuit in shaping
        # TODO format last active time label
        self.booth.last_active_label["text"] = f"Last Active: {self.session_time[0]}:{self.session_time[1]}"
        self.trial_response = "Hit"
        self.pellet_signal.send()

    def handle_pellet(self, sender):
        # Campden + Arduino
        self.booth.client.board.digital[self.booth.booth_info["Pellet Trigger"]].write(1)
        time.sleep(0.01)
        self.booth.client.board.digital[self.booth.booth_info["Pellet Trigger"]].write(0)
        self.num_pellets += 1
        self.end_trial()

    def update_plots(self):
        self.plots["Shaping"].plot.plot(self.session_time[0] + (self.session_time[1] / 60),
                                        self.num_pellets, 'ko', ms=8)
        self.plots["Shaping"].fig.tight_layout()
        self.plots["Shaping"].canvas.draw()


class ToneDetectionTask(task.GoNoGoTask):
    def __init__(self, booth):
        super().__init__(booth)

        self.save_filepath = Path(__file__).parent / f"../../data/ATAT/{self.booth.task_id}/"
        self.upload_info = {"ID": self.booth.client.sheets_ids["ATAT Behavior"]}

        self.circuit_path = Path(__file__).parent / "../../resources/circuits/ATAT_Tone_detection.rcx"
        self.circuit = DSPCircuit(self.circuit_path, "RP2", device_id=self.booth.booth_info["RP2"],
                                  interface="USB", start=True, address=self.booth.client.tdt_rpc_address)
        print("Loaded circuit!")
        self.response_buffer = self.circuit.get_buffer("ltimes", "r", src_type="int32", idx_tag="lpress")
        self.circuit.set_tags(light=0)

        if self.booth_num < 10:
            booth_str = f"0{self.booth_num}"
        else:
            booth_str = f"{self.booth_num}"
        calibrations_path = Path(__file__).parent / f"../../resources/tasks/ATAT_B{booth_str}_speaker_amps.csv"
        self.tone_calibrations = pd.read_csv(calibrations_path)

        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.5, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.5}]

    def prep_trial(self):
        # Randomly select trial sound based on weighted probability
        sound_list = [*self.cs_plus, *self.cs_minus, *self.silence]
        sound_weights = [sound["Weight"] for sound in sound_list]
        self.trial_sound = np.random.choice(sound_list, 1, p=sound_weights)[0]

        if self.trial_sound in self.silence:
            is_silent = 1
            amp = 0
            freq = 0
        else:
            is_silent = 0
            freq = self.trial_sound["Freq"]
            intensity = self.trial_sound["Int"]
            amp = self.tone_calibrations.loc[(self.tone_calibrations["Freq"] == freq) &
                                             (self.tone_calibrations["Int"] == intensity), "Amp"].values[0]

        self.circuit.set_tags(tone_freq=freq, tone_amp=amp, silent=is_silent, light=1)


class RapidAdaptToneDetectionTask(ToneDetectionTask):
    def __init__(self, booth):
        super().__init__(booth)
        self.trial_interval = (self.hit_win_dur + self.hit_win_start) / 1000
        self.timeout_length = 2.0
        self.misses_before_break = 20
        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.8, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.2}]

    def prep_trial(self):
        # Adjust hit window, trial, and timeout durations as session goes on
        # Adjust sound presentation weights as session goes on

        if 10 <= self.session_time[0] < 20:
            self.timeout_length = 3.0
            self.misses_before_break = 15

            # 70% CS+, 30% Silence
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.7, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.3}]

        elif 20 <= self.session_time[0] < 30:
            self.hit_win_dur = 4000
            self.trial_interval = (self.hit_win_dur + self.hit_win_start) / 1000
            self.misses_before_break = 10

            # 70% CS+, 30% Silence
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.6, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.4}]

        elif 30 <= self.session_time[0] < 40:
            self.hit_win_dur = 5000
            self.trial_interval = (self.hit_win_dur + self.hit_win_start) / 1000
            self.timeout_length = 5.0

        elif 40 <= self.session_time[0]:  # Standard task
            self.hit_win_dur = 6000
            self.trial_interval = 8.0
            self.timeout_length = 6.0
            self.misses_before_break = 5

            # 30% CS+, 10% Silence
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.5, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.5}]

        # Run normal discrimination prep_trial()
        super().prep_trial()


class EasyToneDiscriminationTask(ToneDetectionTask):
    def __init__(self, booth):
        super().__init__(booth)
        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.4, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.2}]
        self.cs_minus = [{"Name": "11314 Hz", "Weight": 0.4, "Freq": 11314, "Int": 60}]


class MediumToneDiscriminationTask(EasyToneDiscriminationTask):
    def __init__(self, booth):
        super().__init__(booth)
        self.cs_minus = [{"Name": "4000 Hz", "Weight": 0.4, "Freq": 4000, "Int": 60}]


class RapidAdaptEasyToneDiscriminationTask(EasyToneDiscriminationTask):
    def __init__(self, booth):
        super().__init__(booth)
        self.trial_interval = (self.hit_win_dur + self.hit_win_start) / 1000
        self.timeout_length = 2.0
        self.misses_before_break = 20
        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.16, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.0}]
        self.cs_minus = [{"Name": "11314 Hz", "Weight": 0.84, "Freq": 11314, "Int": 60}]

    def prep_trial(self):
        # Adjust hit window, trial, and timeout durations as session goes on
        # Adjust sound presentation weights as session goes on

        if 10 <= self.session_time[0] < 20:
            self.timeout_length = 3.0
            self.misses_before_break = 15

            # 28% CS+
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.28, "Freq": 2000, "Int": 60}]
            self.cs_minus = [{"Name": "11314 Hz", "Weight": 0.72, "Freq": 11314, "Int": 60}]

        elif 20 <= self.session_time[0] < 30:
            self.trial_interval = 4.0
            self.misses_before_break = 10

            # 33% CS+
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.33, "Freq": 2000, "Int": 60}]
            self.cs_minus = [{"Name": "11314 Hz", "Weight": 0.67, "Freq": 11314, "Int": 60}]

        elif 30 <= self.session_time[0] < 40:
            self.trial_interval = 5.0
            self.timeout_length = 5.0

            # 33% CS+, 17% Silence, 50% CS-
            self.silence = [{"Name": "Silence", "Weight": 0.17}]
            self.cs_minus = [{"Name": "11314 Hz", "Weight": 0.5, "Freq": 11314, "Int": 60}]

        elif 40 <= self.session_time[0]:  # Standard task
            self.trial_interval = 8.0
            self.timeout_length = 6.0
            self.misses_before_break = 5

            # 40% CS+, 20% Silence, 40% CS-
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.4, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.20}]
            self.cs_minus = [{"Name": "11314 Hz", "Weight": 0.4, "Freq": 11314, "Int": 60}]

        # Run normal discrimination prep_trial()
        super().prep_trial()


class ToneDiscriminationTask(ToneDetectionTask):
    def __init__(self, booth):
        super().__init__(booth)

        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.4, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.1}]
        self.cs_minus = [
            {"Name": "2182 Hz", "Weight": 0.1, "Freq": 2182, "Int": 60},
            {"Name": "2378 Hz", "Weight": 0.1, "Freq": 2378, "Int": 60},
            {"Name": "2828 Hz", "Weight": 0.1, "Freq": 2828, "Int": 60},
            {"Name": "4000 Hz", "Weight": 0.1, "Freq": 4000, "Int": 60},
            {"Name": "11314 Hz", "Weight": 0.1, "Freq": 11314, "Int": 60},
        ]


class RapidAdaptToneDiscriminationTask(ToneDiscriminationTask):
    def __init__(self, booth):
        super().__init__(booth)
        self.trial_interval = (self.hit_win_dur + self.hit_win_start) / 1000
        self.timeout_length = 2.0
        self.misses_before_break = 20
        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.15, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.0}]
        self.cs_minus = [
            {"Name": "2182 Hz", "Weight": 0.3, "Freq": 2182, "Int": 60},
            {"Name": "2378 Hz", "Weight": 0.4, "Freq": 2378, "Int": 60},
            {"Name": "2828 Hz", "Weight": 0.15, "Freq": 2828, "Int": 60},
            {"Name": "4000 Hz", "Weight": 0.0, "Freq": 4000, "Int": 60},
            {"Name": "11314 Hz", "Weight": 0.0, "Freq": 11314, "Int": 60},
        ]

    def prep_trial(self):
        # Adjust hit window, trial, and timeout durations as session goes on
        # Adjust sound presentation weights as session goes on

        if 10 <= self.session_time[0] < 20:
            self.timeout_length = 3.0
            self.misses_before_break = 15

            # 20% CS+, 0% Silence, emphasize harder discriminations first
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.3, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.0}]
            self.cs_minus = [
                {"Name": "2182 Hz", "Weight": 0.2, "Freq": 2182, "Int": 60},
                {"Name": "2378 Hz", "Weight": 0.5, "Freq": 2378, "Int": 60},
                {"Name": "2828 Hz", "Weight": 0.0, "Freq": 2828, "Int": 60},
                {"Name": "4000 Hz", "Weight": 0.0, "Freq": 4000, "Int": 60},
                {"Name": "11314 Hz", "Weight": 0.0, "Freq": 11314, "Int": 60},
            ]

        elif 20 <= self.session_time[0] < 30:
            self.trial_interval = 4.0
            self.misses_before_break = 10

            # 20% CS+, 0% Silence, emphasize harder discriminations first
            self.cs_minus = [
                {"Name": "2182 Hz", "Weight": 0.2, "Freq": 2182, "Int": 60},
                {"Name": "2378 Hz", "Weight": 0.3, "Freq": 2378, "Int": 60},
                {"Name": "2828 Hz", "Weight": 0.1, "Freq": 2828, "Int": 60},
                {"Name": "4000 Hz", "Weight": 0.1, "Freq": 4000, "Int": 60},
                {"Name": "11314 Hz", "Weight": 0.0, "Freq": 11314, "Int": 60},
            ]
        elif 30 <= self.session_time[0] < 40:
            self.trial_interval = 5.0
            self.timeout_length = 5.0

            # 30% CS+, 10% Silence
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.4, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.0}]
            self.cs_minus = [
                {"Name": "2182 Hz", "Weight": 0.2, "Freq": 2182, "Int": 60},
                {"Name": "2378 Hz", "Weight": 0.1, "Freq": 2378, "Int": 60},
                {"Name": "2828 Hz", "Weight": 0.1, "Freq": 2828, "Int": 60},
                {"Name": "4000 Hz", "Weight": 0.1, "Freq": 4000, "Int": 60},
                {"Name": "11314 Hz", "Weight": 0.1, "Freq": 11314, "Int": 60},
            ]
        elif 40 <= self.session_time[0]:  # Standard task
            self.trial_interval = 8.0
            self.timeout_length = 6.0
            self.misses_before_break = 5

            # 30% CS+, 10% Silence
            self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.4, "Freq": 2000, "Int": 60}]
            self.silence = [{"Name": "Silence", "Weight": 0.1}]
            self.cs_minus = [
                {"Name": "2182 Hz", "Weight": 0.1, "Freq": 2182, "Int": 60},
                {"Name": "2378 Hz", "Weight": 0.1, "Freq": 2378, "Int": 60},
                {"Name": "2828 Hz", "Weight": 0.1, "Freq": 2828, "Int": 60},
                {"Name": "4000 Hz", "Weight": 0.1, "Freq": 4000, "Int": 60},
                {"Name": "11314 Hz", "Weight": 0.1, "Freq": 11314, "Int": 60},
            ]

        # Run normal discrimination prep_trial()
        super().prep_trial()


class QuestDiscriminationTask(ToneDetectionTask):
    def __init__(self, booth):
        super().__init__(booth)

        # Use tone calibrations for this task
        if self.booth_num < 10:
            booth_str = f"0{self.booth_num}"
        else:
            booth_str = f"{self.booth_num}"
        calibrations_path = Path(__file__).parent / f"../../resources/tasks/psyc-ATAT_B{booth_str}_speaker_amps.csv"
        self.tone_calibrations = pd.read_csv(calibrations_path)

        self.cs_minus_freqs = np.round(2000 * 2 ** (np.arange(1, 31) / 12)).astype(int)

        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.45, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.1}]
        self.quest_handler = QuestPlusHandler(
            nTrials=200,
            intensityVals=self.cs_minus_freqs,
            thresholdVals=self.cs_minus_freqs,
            slopeVals=np.linspace(300, 5000, 50),
            lowerAsymptoteVals=np.linspace(0, 0.4, 15),
            lapseRateVals=np.linspace(0, 0.2, 11),
            responseVals=[1, 0],
            stimScale="linear",
        )
        cs_minus_freq = self.quest_handler.next()
        self.cs_minus = [{"Name": f"{cs_minus_freq} Hz", "Weight": 0.45, "Freq": cs_minus_freq, "Int": 60}]

    def prep_trial(self):
        # Check if last sound was a CS-. If so, update QUEST+ and select new CS-
        if self.trial_sound in self.cs_minus:
            if self.trial_response == "Correct rejection":
                self.quest_handler.addResponse(1)
            else:
                self.quest_handler.addResponse(0)

        try:
            cs_minus_freq = self.quest_handler.next()
            self.cs_minus = [{"Name": f"{cs_minus_freq} Hz", "Weight": 0.45, "Freq": cs_minus_freq, "Int": 60}]
        except StopIteration:
            pass

        # Run normal prep
        super().prep_trial()

    def save(self, temp=False, filepath=None, filename=None):
        # Pickle Quest object
        if not temp:
            quest_filepath = Path(__file__).parent / "../../data/quest/"
            quest_filepath.mkdir(parents=True, exist_ok=True)
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            session_num = 1
            quest_filename = f"{self.booth.rat}_{today}_Session-{session_num}.pickle"
            while Path.is_file(quest_filepath / quest_filename):
                session_num += 1
                quest_filename = f"{self.booth.rat}_{today}_Session-{session_num}.pickle"
            self.quest_handler.saveAsPickle(str(quest_filepath / quest_filename), fileCollisionMethod="overwrite")

        super().save(temp=temp, filepath=filepath, filename=filename)

    def update_session_data(self):
        # TODO Temporary overwriting so program doesn't complain about unknown CS-'s
        if self.session_data.empty:  # Handle first trial special to set up columns and values
            data_dict = {}
            for sound in ["CS+", "CS-", *self.cs_plus, *self.cs_minus_freqs, *self.silence]:
                if isinstance(sound, dict):
                    sound_name = sound["Name"]
                elif sound in self.cs_minus_freqs:
                    sound_name = f"{sound} Hz"
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

    def setup_plots(self):
        # TODO Same as above. Work out a better integrated system once task is running
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
        for sound in [*self.cs_plus, *self.cs_minus_freqs, *self.silence]:
            if sound in self.cs_plus:
                color = "xkcd:green"
            elif sound in self.silence:
                color = "xkcd:red"
            else:
                color = "xkcd:blue"

            if isinstance(sound, dict):
                sound_name = sound["Name"]
            else:  # For the CS- freqs
                sound_name = f"{sound} Hz"

            x_ticks.append(x_pos)
            x_labels.append(sound_name)

            bar = self.plots["Response"].response_percent_plot.bar(x_pos, 0, color=color).patches[0]
            x_pos += 1
            bar.set_label(sound_name)
            self.plots["Response"].percent_bars[sound_name] = bar

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


class PsiDiscriminationTask(ToneDetectionTask):
    def __init__(self, booth):
        super().__init__(booth)

        # Use tone calibrations for this task
        if self.booth_num < 10:
            booth_str = f"0{self.booth_num}"
        else:
            booth_str = f"{self.booth_num}"
        calibrations_path = Path(__file__).parent / f"../../resources/tasks/psyc-ATAT_B{booth_str}_speaker_amps.csv"
        self.tone_calibrations = pd.read_csv(calibrations_path)

        self.cs_minus_freqs = np.round(2000 * 2 ** (np.arange(1, 31) / 12)).astype(int)
        self.psi_thresholds = (self.cs_minus_freqs[1:] + self.cs_minus_freqs[:-1]) / 2
        self.psi_ntrials = 200
        self.psi_slopes = np.linspace(50, 4000, 50)
        self.psi_guess = np.linspace(0.05, 0.4, 4)
        self.psi_lapse = np.linspace(0, 0.3, 10)

        self.cs_plus = [{"Name": "2000 Hz", "Weight": 0.45, "Freq": 2000, "Int": 60}]
        self.silence = [{"Name": "Silence", "Weight": 0.1}]
        self.psi_handler = PsiMarginal.Psi(self.cs_minus_freqs,
                                           Pfunction="cGauss",
                                           nTrials=self.psi_ntrials,
                                           threshold=self.psi_thresholds,
                                           slope=self.psi_slopes,
                                           guessRate=self.psi_guess,
                                           lapseRate=self.psi_lapse,
                                           marginalize=True,
                                           )
        while self.psi_handler.xCurrent is None:
            time.sleep(0.1)
        cs_minus_freq = self.psi_handler.xCurrent
        self.cs_minus = [{"Name": f"{cs_minus_freq} Hz", "Weight": 0.45, "Freq": cs_minus_freq, "Int": 60}]

    def prep_trial(self):
        # Check if last sound was a CS-. If so, update Psi and select new CS-
        # Treat 'Early' and 'Late' as aborts
        if self.trial_sound in self.cs_minus:
            if self.trial_response == "Correct rejection":
                self.psi_handler.addData(1)
            elif self.trial_response == "False alarm":
                self.psi_handler.addData(0)

        try:
            while self.psi_handler.xCurrent is None:
                time.sleep(0.1)
            cs_minus_freq = self.psi_handler.xCurrent
            self.cs_minus = [{"Name": f"{cs_minus_freq} Hz", "Weight": 0.45, "Freq": cs_minus_freq, "Int": 60}]
        except StopIteration:
            pass

        # Run normal prep
        super().prep_trial()

    def save(self, temp=False, filepath=None, filename=None):
        # Pickle Psi object if end of session
        if not temp:
            psi_filepath = Path(__file__).parent / "../../data/psi/"
            psi_filepath.mkdir(parents=True, exist_ok=True)
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            session_num = 1
            psi_filename = f"{self.booth.rat}_{today}_Session-{session_num}.pickle"
            while Path.is_file(psi_filepath / psi_filename):
                session_num += 1
                psi_filename = f"{self.booth.rat}_{today}_Session-{session_num}.pickle"

            with (psi_filepath / psi_filename).open(mode="wb") as file:
                pickle.dump(self.psi_handler, file)

        super().save(temp=temp, filepath=filepath, filename=filename)

    def update_session_data(self):
        # TODO Temporary overwriting so program doesn't complain about unknown CS-'s
        if self.session_data.empty:  # Handle first trial special to set up columns and values
            data_dict = {}
            for sound in ["CS+", "CS-", *self.cs_plus, *self.cs_minus_freqs, *self.silence]:
                if isinstance(sound, dict):
                    sound_name = sound["Name"]
                elif sound in self.cs_minus_freqs:
                    sound_name = f"{sound} Hz"
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

    def setup_plots(self):
        # TODO Same as above. Work out a better integrated system once task is running
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
        for sound in [*self.cs_plus, *self.cs_minus_freqs, *self.silence]:
            if sound in self.cs_plus:
                color = "xkcd:green"
            elif sound in self.silence:
                color = "xkcd:red"
            else:
                color = "xkcd:blue"

            if isinstance(sound, dict):
                sound_name = sound["Name"]
            else:  # For the CS- freqs
                sound_name = f"{sound} Hz"

            x_ticks.append(x_pos)
            x_labels.append(sound_name)

            bar = self.plots["Response"].response_percent_plot.bar(x_pos, 0, color=color).patches[0]
            x_pos += 1
            bar.set_label(sound_name)
            self.plots["Response"].percent_bars[sound_name] = bar

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


class PsiDetectionTask(ToneDetectionTask):
    def __init__(self, booth):
        super().__init__(booth)

        # Use tone calibrations for this task
        if self.booth_num < 10:
            booth_str = f"0{self.booth_num}"
        else:
            booth_str = f"{self.booth_num}"
        calibrations_path = Path(__file__).parent / f"../../resources/tasks/psyc-ATAT_B{booth_str}_speaker_amps.csv"
        self.tone_calibrations = pd.read_csv(calibrations_path)

        self.cs_plus_ints = np.arange(0, 77, 3)
        self.psi_thresholds = (self.cs_plus_ints[1:] + self.cs_plus_ints[:-1]) / 2
        self.psi_ntrials = 200
        self.psi_slopes = np.linspace(0, 30, 20)
        self.psi_guess = np.linspace(0.05, 0.4, 4)
        self.psi_lapse = np.linspace(0, 0.3, 10)

        self.silence = [{"Name": "Silence", "Weight": 0.5}]
        self.psi_handler = PsiMarginal.Psi(self.cs_plus_ints,
                                           Pfunction="cGauss",
                                           nTrials=self.psi_ntrials,
                                           threshold=self.psi_thresholds,
                                           slope=self.psi_slopes,
                                           guessRate=self.psi_guess,
                                           lapseRate=self.psi_lapse,
                                           marginalize=True,
                                           )
        while self.psi_handler.xCurrent is None:
            time.sleep(0.1)
        cs_plus_int = self.psi_handler.xCurrent
        self.cs_plus = [{"Name": f"2000 Hz {cs_plus_int} dB", "Weight": 0.5, "Freq": 2000, "Int": cs_plus_int}]

    def prep_trial(self):
        # Check if last sound was a CS+. If so, update Psi and select new CS+ intensity
        # Treat 'Early' and 'Late' as aborts
        if self.trial_sound in self.cs_plus:
            if self.trial_response == "Hit":
                self.psi_handler.addData(1)
            elif self.trial_response == "Miss":
                self.psi_handler.addData(0)

        try:
            while self.psi_handler.xCurrent is None:
                time.sleep(0.1)
            cs_plus_int = self.psi_handler.xCurrent
            self.cs_plus = [{"Name": f"2000 Hz {cs_plus_int} dB", "Weight": 0.5, "Freq": 2000, "Int": cs_plus_int}]
        except StopIteration:
            pass

        # Run normal prep
        super().prep_trial()

    def save(self, temp=False, filepath=None, filename=None):
        # Pickle Psi object if end of session
        if not temp:
            psi_filepath = Path(__file__).parent / "../../data/psi/"
            psi_filepath.mkdir(parents=True, exist_ok=True)
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            session_num = 1
            psi_filename = f"{self.booth.rat}_{today}_Session-{session_num}.pickle"
            while Path.is_file(psi_filepath / psi_filename):
                session_num += 1
                psi_filename = f"{self.booth.rat}_{today}_Session-{session_num}.pickle"

            with (psi_filepath / psi_filename).open(mode="wb") as file:
                pickle.dump(self.psi_handler, file)

        super().save(temp=temp, filepath=filepath, filename=filename)

    def update_session_data(self):
        # TODO Temporary overwriting so program doesn't complain about unknown CS+ intensities
        if self.session_data.empty:  # Handle first trial special to set up columns and values
            data_dict = {}
            for sound in ["CS+", "CS-", *self.cs_plus_ints, *self.silence]:
                if isinstance(sound, dict):
                    sound_name = sound["Name"]
                elif sound in self.cs_plus_ints:
                    sound_name = f"2000 Hz {sound} dB"
                else:
                    sound_name = sound
                data_dict[f"{sound_name} Hits"] = 0
                data_dict[f"{sound_name} Trials"] = 0
                data_dict[f"{sound_name} % Hit"] = np.nan
                if sound not in ["CS+", *self.cs_plus, *self.cs_plus_ints]:
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

    def setup_plots(self):
        # TODO Same as above. Work out a better integrated system once task is running
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
        for sound in [*self.cs_plus_ints, *self.silence]:
            if sound in self.cs_plus_ints:
                color = "xkcd:green"
            elif sound in self.silence:
                color = "xkcd:red"
            else:
                color = "xkcd:blue"

            if isinstance(sound, dict):
                sound_name = sound["Name"]
            else:  # For the CS+ ints
                sound_name = f"2000 Hz {sound} dB"

            x_ticks.append(x_pos)
            x_labels.append(sound_name)

            bar = self.plots["Response"].response_percent_plot.bar(x_pos, 0, color=color).patches[0]
            x_pos += 1
            bar.set_label(sound_name)
            self.plots["Response"].percent_bars[sound_name] = bar

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


class SpeechDiscriminationTask(task.GoNoGoTask):
    def __init__(self, booth):
        super().__init__(booth)

        self.save_filepath = Path(__file__).parent / f"../../data/ATAT/{self.booth.task_id}/"
        self.upload_info = {"ID": self.booth.client.sheets_ids["ATAT Behavior"]}

        self.circuit_path = Path(__file__).parent / "../../resources/circuits/ATAT_NoiseSpeech_Discrimination.rcx"
        self.circuit = DSPCircuit(self.circuit_path, "RP2", device_id=self.booth.booth_info["RP2"],
                                  interface="USB", start=True, address=self.booth.client.tdt_rpc_address)
        print("Loaded circuit!")
        self.response_buffer = self.circuit.get_buffer("ltimes", "r", src_type="int32", idx_tag="lpress")
        self.circuit.set_tags(light=0)
        self.speech_buffer = self.circuit.get_buffer("data_in", "w", src_type="int32", idx_tag="speech_tag")

        if self.booth_num < 10:
            booth_str = f"0{self.booth_num}"
        else:
            booth_str = f"{self.booth_num}"

        self.cs_plus = [{"Name": "Dad", "Weight": 0.33, "File": "../resources/sounds/SIN/dad.wav"}]
        self.silence = [{"Name": "Silence", "Weight": 0.34, "File": "../resources/sounds/SIN/silence.wav"}]
        self.cs_minus = [
            {"Name": "Bad", "Weight": 0.0825, "File": "../resources/sounds/SIN/bad.wav"},
            {"Name": "Gad", "Weight": 0.0825, "File": "../resources/sounds/SIN/gad.wav"},
            {"Name": "Tad", "Weight": 0.0825, "File": "../resources/sounds/SIN/tad.wav"},
            {"Name": "Sad", "Weight": 0.0825, "File": "../resources/sounds/SIN/sad.wav"},
        ]

        # Add VNS to this behavior task
        self.vns = int(self.booth.client.parameters_info.loc[
            self.booth.client.parameters_info["Rat"] == self.booth.rat, "VNS"].values[0])

    def prep_trial(self):
        # Randomly select trial sound based on weighted probability
        sound_list = [*self.cs_plus, *self.cs_minus, *self.silence]
        sound_weights = [sound["Weight"] for sound in sound_list]
        self.trial_sound = np.random.choice(sound_list, 1, p=sound_weights)[0]

        if self.trial_sound in self.silence:
            is_silent = 1
        else:
            is_silent = 0
        _, data = wavfile.read(self.trial_sound["File"])
        # Normalize -- assumes int16 dtype
        data = data / 32767
        data = np.pad(data, (0, 200000 - data.shape[-1]), "constant")
        self.circuit.set_tags(light=1)
        self.speech_buffer.set(data)

    def handle_pellet(self, _sender):
        if self.vns:
            self.circuit.set_tags(Stim=1)
            time.sleep(0.001)
            self.circuit.set_tags(Stim=0)
            
        super().handle_pellet(_sender)


class SSNSpeechDiscriminationTask(SpeechDiscriminationTask):
    def __init__(self, booth):
        super().__init__(booth)

        self.speech_noise = np.genfromtxt("../resources/sounds/SIN/filtered_ssn.csv", delimiter=",")
        self.noise_buffer = self.circuit.get_buffer("noise_in", "w", idx_tag="noise_tag")
        self.noise_levels = [0, 0.05, 0.12]
        self.current_noise_level = np.random.choice([0.05, 0.12], 1)[0]
        ramp = np.linspace(0, self.current_noise_level, 100000) * self.speech_noise[:100000]
        ramped_noise = np.concatenate((ramp, self.speech_noise * self.current_noise_level))
        self.noise_buffer.set(ramped_noise[:200000])
        self.noise_buffer.set(ramped_noise[200000:])

        self.noise_block_number = 1
        self.noise_trial_block = 20
        self.noise_trial_counter = 0

    def prep_trial(self):
        super().prep_trial()
        if self.noise_trial_block <= self.noise_trial_counter:
            previous_noise_level = self.current_noise_level
            self.current_noise_level = np.random.choice(list({*self.noise_levels} ^ {previous_noise_level}), 1)[0]
            ramp = np.linspace(previous_noise_level, self.current_noise_level, 100000) * self.speech_noise[:100000]
            ramped_noise = np.concatenate((ramp, self.speech_noise * self.current_noise_level))
            self.noise_buffer.set(ramped_noise[:200000])
            self.noise_buffer.set(ramped_noise[200000:])
            self.noise_trial_counter = 0
            self.noise_block_number += 1
            time.sleep(10)  # Hang out for 10 sec to give rat time to adjust

        self.noise_trial_counter += 1

    def stop_session(self):
        ramp = np.linspace(self.current_noise_level, 0., 100000) * self.speech_noise[:100000]
        ramped_noise = np.concatenate((ramp, self.speech_noise * 0.))
        self.noise_buffer.set(ramped_noise[:200000])
        self.noise_buffer.set(ramped_noise[200000:])
        time.sleep(5)  # Let noise ramp down before stopping circuit
        super().stop_session()

    def update_session_data(self):
        # TODO super ugly, but I just need a quick way to ensure trial noise level is saved
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
        data_dict["Noise Level"] = self.current_noise_level
        data_dict["Noise Block"] = self.noise_block_number
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
