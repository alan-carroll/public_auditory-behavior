import tasks.ATAT_tasks
from scipy.stats import norm
import numpy as np


def get_task(task_id, booth):
    if task_id == "ATAT_Shaping":
        return tasks.ATAT_tasks.ToneShapingTask(booth)
    elif task_id == "ATAT_Rapid_Detection":
        return tasks.ATAT_tasks.RapidAdaptToneDetectionTask(booth)
    elif task_id == "ATAT_Detection":
        return tasks.ATAT_tasks.ToneDetectionTask(booth)
    elif task_id == "ATAT_Rapid_Easy_Discrimination":
        return tasks.ATAT_tasks.RapidAdaptEasyToneDiscriminationTask(booth)
    elif task_id == "ATAT_Easy_Discrimination":
        return tasks.ATAT_tasks.EasyToneDiscriminationTask(booth)
    elif task_id == "ATAT_Medium_Discrimination":
        return tasks.ATAT_tasks.MediumToneDiscriminationTask(booth)
    elif task_id == "ATAT_Rapid_Discrimination":
        return tasks.ATAT_tasks.RapidAdaptToneDiscriminationTask(booth)
    elif task_id == "ATAT_Discrimination":
        return tasks.ATAT_tasks.ToneDiscriminationTask(booth)
    elif task_id == "ATAT_Quest_Discrimination":
        return tasks.ATAT_tasks.QuestDiscriminationTask(booth)
    elif task_id == "ATAT_Psi_Discrimination":
        return tasks.ATAT_tasks.PsiDiscriminationTask(booth)
    elif task_id == "ATAT_Psi_Detection":
        return tasks.ATAT_tasks.PsiDetectionTask(booth)
    elif task_id == "ATAT_Speech":
        return tasks.ATAT_tasks.SpeechDiscriminationTask(booth)
    elif task_id == "ATAT_SIN":
        return tasks.ATAT_tasks.SSNSpeechDiscriminationTask(booth)


def update_percent_hit(previous_percent, num_trials, hit):
    return (previous_percent * (num_trials - 1) + (hit * 100)) / num_trials


def calc_d_prime(hits, misses, false_alarms, correct_rejections):
    z = norm.ppf
    # Uses a correction factor to avoid d' infinity
    if ((hits + misses) == 0) or ((false_alarms + correct_rejections) == 0):
        return np.nan
    hit_correction = 0.5 / (hits + misses)
    fa_correction = 0.5 / (false_alarms + correct_rejections)

    # Hit rate
    hit_rate = hits / (hits + misses)
    if hit_rate == 1:
        hit_rate = 1 - hit_correction
    elif hit_rate == 0:
        hit_rate = hit_correction

    # False alarm rate
    fa_rate = false_alarms / (false_alarms + correct_rejections)
    if fa_rate == 1:
        fa_rate = 1 - fa_correction
    elif fa_rate == 0:
        fa_rate = fa_correction

    return z(hit_rate) - z(fa_rate)
