#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Class for array of spike trains.

@author: David Samu
"""

import numpy as np
import pandas as pd
from quantities import s, ms
from neo import SpikeTrain
from elephant import statistics

from seal.util import util


class Spikes:
    """Class for storing spike trains per trial and associated properties."""

    # %% Constructor
    def __init__(self, spk_trains, t_starts=None, t_stops=None):
        """Create a Spikes instance."""

        # Create empty instance.
        self.spk_trains = None
        self.t_starts = None
        self.t_stops = None

        # Init t_starts and t_stops.
        # Below deals with single values, including None.
        n_trs = len(spk_trains)
        if not util.is_iterable(t_starts):
            t_starts = n_trs * [t_starts]
        if not util.is_iterable(t_stops):
            t_stops = n_trs * [t_stops]

        # Convert into Pandas Series for speed and more functionality.
        self.t_starts = pd.Series(t_starts)
        self.t_stops = pd.Series(t_stops)

        # Create list of Neo SpikeTrain objects.
        self.spk_trains = pd.Series(index=np.arange(n_trs), dtype=object)
        for i in self.spk_trains.index:

            # Remove spikes outside of time window.
            t_start, t_stop = self.t_starts[i], self.t_stops[i]
            spk_tr = util.values_in_window(spk_trains[i], t_start, t_stop)
            self.spk_trains[i] = SpikeTrain(spk_tr, t_start=t_start,
                                            t_stop=t_stop)

    # %% Utility methods.

    def init_time_limits(self, t1s=None, t2s=None, ref_ts=None):
        """Set time limits to default values if not specified."""

        # Default time limits and reference time.
        if t1s is None:
            t1s = self.t_starts
        if t2s is None:
            t2s = self.t_stops
        if ref_ts is None:
            ref_ts = np.zeros(self.n_trials()) * ms

        return t1s, t2s, ref_ts

    def init_trials(self, trs=None):
        """Set trials to all trials if not specified."""

        if trs is None:
            print('No trial set has been passed. Returning all trials.')
            trs = np.arange(self.n_trials())
        return trs

    def n_trials(self):
        """Return number of trials."""

        n_trs = len(self.spk_trains.index)
        return n_trs

    def get_spikes(self, trs=None, t1s=None, t2s=None, ref_ts=None):
        """Return spike times of given trials within time windows."""

        # Init trials and time limits.
        trs = self.init_trials(trs)
        t1s, t2s, ref_ts = self.init_time_limits(t1s, t2s, ref_ts)

        # Assamble time-windowed spike trains.
        spk_trains = pd.Series(index=trs, dtype=object)
        for itr in trs:
            # Select spikes between t1 and t2 during selected trials, and
            # convert them into new SpikeTrain list, with time limits set.
            t1, t2, tr = t1s[itr], t2s[itr], ref_ts[itr]
            spk_tr = util.values_in_window(self.spk_trains[itr], t1, t2)
            spk_tr, t1, t2 = spk_tr-tr, t1-tr, t2-tr  # align to reference time
            # Need to check range once again to deal with rounding errors.
            spk_tr = util.values_in_window(spk_tr, t1, t2)
            spk_trains[itr] = SpikeTrain(spk_tr, t_start=t1, t_stop=t2)

        return spk_trains

    # %% Methods for summary statistics over spikes.

    def n_spikes(self, trs=None, t1s=None, t2s=None):
        """Return spike count of given trials in time windows."""

        # Default time limits.
        t1s, t2s, _ = self.init_time_limits(t1s, t2s)

        # Select spikes within windows.
        spk_trains = self.get_spikes(trs, t1s, t2s)

        # Count spikes during each selected trial.
        n_spikes = spk_trains.apply(np.size)

        return n_spikes

    def rates(self, trs=None, t1s=None, t2s=None):
        """Return rates of given trials in time windows."""

        t1s, t2s, _ = self.init_time_limits(t1s, t2s)
        trs = self.init_trials(trs)

        # Get number of spikes.
        n_spikes = self.n_spikes(trs, t1s, t2s)

        # Rescale time limits.
        t1s_sec = util.rescale_series(t1s[trs], s)
        t2s_sec = util.rescale_series(t2s[trs], s)

        # Calculate rates for each selected trial.
        rates = n_spikes / (t2s_sec - t1s_sec)

        return rates

    def isi(self, trs=None, t1s=None, t2s=None):
        """Return interspike intervals per trial."""

        spks = self.get_spikes(trs, t1s, t2s)
        isi = [statistics.isi(spk) for spk in spks]

        return isi
