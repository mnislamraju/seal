# -*- coding: utf-8 -*-
"""
Created on Sat Aug 20 14:06:14 2016

Class representing a (spike sorted) unit (single or multi).

@author: David Samu
"""

import warnings

from datetime import datetime as dt

import numpy as np
import pandas as pd
from quantities import s, ms, us, deg, Hz

from seal.util import util
from seal.plot import prate, ptuning
from seal.object import constants
from seal.object.rate import Rate
from seal.object.spikes import Spikes
from seal.analysis import tuning


class Unit:
    """Generic class to store data of a unit (neuron or group of neurons)."""

    # %% Constructor
    def __init__(self, TPLCell=None, kernels=None, step=None, stim_params=None,
                 answ_params=None, stim_dur=None, tr_evt=None, taskname=None,
                 region=None):
        """Create Unit instance from TPLCell data structure."""

        # Create empty instance.
        self.Name = ''
        self.UnitParams = pd.Series()
        self.SessParams = pd.Series()
        self.Waveforms = pd.DataFrame()
        self.SpikeParams = pd.DataFrame()
        self.StimParams = pd.DataFrame()
        self.Answer = pd.DataFrame()
        self.Events = pd.DataFrame()
        self.TrialParams = pd.DataFrame()
        self.Spikes = Spikes([])
        self.Rates = pd.Series()
        self.QualityMetrics = pd.Series()
        self.DS = pd.Series()

        # Default unit params.
        self.UnitParams['region'] = region
        self.UnitParams['empty'] = True
        self.UnitParams['excluded'] = True

        # Return if no TPLCell is passed.
        if TPLCell is None:
            return

        # %% Session parameters.

        # Prepare session params.
        subj, date, probe, exp, isort = util.params_from_fname(TPLCell.File)
        task, task_idx = exp[:-1], int(exp[-1])
        task = task if taskname is None else taskname  # use provided name
        [chan, un] = TPLCell.ChanUnit
        sampl_prd = (1 / (TPLCell.Info.Frequency * Hz)).rescale(us)
        pinfo = [p.tolist() if isinstance(p, np.ndarray)
                 else p for p in TPLCell.PInfo]
        sess_date = dt.date(dt.strptime(date, '%m%d%y'))
        recording = subj + '_' + util.date_to_str(sess_date)

        # Assign session params.
        sp_list = [('task', task),
                   ('task #', task_idx),
                   ('subject', subj),
                   ('date', sess_date),
                   ('recording', recording),
                   ('probe', probe),
                   ('channel #', chan),
                   ('unit #', un),
                   ('sort #', isort),
                   ('filepath', TPLCell.Filename),
                   ('filename', TPLCell.File),
                   ('paraminfo', pinfo),
                   ('sampl_prd', sampl_prd)]
        self.SessParams = util.series_from_tuple_list(sp_list)

        # Name unit.
        self.set_name()

        # %% Waveforms.

        wfs = TPLCell.Waves
        if wfs.ndim == 1:  # there is only a single spike: extend it to matrix
            wfs = np.reshape(wfs, (1, len(wfs)))
        wf_sampl_t = float(sampl_prd) * np.arange(wfs.shape[1])
        self.Waveforms = pd.DataFrame(wfs, columns=wf_sampl_t)

        # %% Spike params.

        spk_pars = [('time', util.fill_dim(TPLCell.Spikes)),
                    ('dur', util.fill_dim(TPLCell.Spikes_dur * s.rescale(us))),
                    ('included', True)]
        self.SpikeParams = pd.DataFrame.from_items(spk_pars)

        # %% Stimulus parameters.

        # Extract all trial parameters.
        trpars = pd.DataFrame(TPLCell.TrialParams, columns=TPLCell.Header)

        # Extract stimulus parameters.
        self.StimParams = trpars[stim_params.name]
        self.StimParams.columns = stim_params.index

        # %% Subject answer parameters.

        # Recode correct/incorrect answer column.
        corr_ans = trpars[answ_params['AnswCorr']]
        if len(corr_ans.unique()) > 2:
            warnings.warn(('More than 2 unique values in AnswCorr: ' +
                           corr_ans.unique()))
        corr_ans = corr_ans == corr_ans.max()  # higher value is correct!
        self.Answer['AnswCorr'] = corr_ans

        # Add column for subject response (saccade direction).
        same_dir = self.StimParams['S1', 'Dir'] == self.StimParams['S2', 'Dir']
        # This is not actually correct for passive task!
        self.Answer['SaccadeDir'] = ((same_dir & corr_ans) |
                                     (~same_dir & ~corr_ans))

        # %% Trial events.

        # Timestamps of events. Only S1 offset and S2 onset are reliable!
        # S1 onset and S2 offset are fixed to these two.
        # Altogether these four are called anchor events.

        # Watch out: indexing starting with 1 in TPLCell (Matlab)!
        # Everything is in seconds below!

        S1dur = float(stim_dur['S1'].rescale(s))
        S2dur = float(stim_dur['S2'].rescale(s))
        iS1off = TPLCell.Patterns.matchedPatterns[:, 2]-1
        iS2on = TPLCell.Patterns.matchedPatterns[:, 3]-1
        anchor_evts = [('S1 on', TPLCell.Timestamps[iS1off]-S1dur),
                       ('S1 off', TPLCell.Timestamps[iS1off]),
                       ('S2 on', TPLCell.Timestamps[iS2on]),
                       ('S2 off', TPLCell.Timestamps[iS2on]+S2dur)]
        anchor_evts = pd.DataFrame.from_items(anchor_evts)

        # Align trial events to S1 onset.
        abs_S1_onset = anchor_evts['S1 on']  # this is also used below!
        anchor_evts = anchor_evts.subtract(abs_S1_onset, axis=0)

        # Add additional trial events, relative to anchor events.
        evts = [(evt, anchor_evts[rel]+float(offset.rescale(s)))
                for evt, (rel, offset) in tr_evt.iterrows()]
        evts = pd.DataFrame.from_items(evts)

        # Add dimension to timestamps (ms).
        for evt in evts:
            evts[evt] = util.add_dim_to_series(1000*evts[evt], ms)  # s --> ms
        self.Events = evts

        # %% Trial parameters.

        # Add start time, end time and length of each trials.
        tstamps = TPLCell.Timestamps
        tr_times = np.array([(tstamps[i1-1], tstamps[i2-1]) for i1, i2
                             in TPLCell.Info.successfull_trials_indices]) * s
        for colname, col in [('TrialStart', tr_times[:, 0]),
                             ('TrialStop', tr_times[:, 1]),
                             ('TrialLength', tr_times[:, 1] - tr_times[:, 0])]:
            util.add_quant_col(self.TrialParams, col, colname)

        # Add trial period lengths to trial params.
        self.TrialParams['S1Len'] = evts['S1 off'] - evts['S1 on']
        self.TrialParams['S2Len'] = evts['S2 off'] - evts['S2 on']
        self.TrialParams['DelayLenPrec'] = evts['S2 on'] - evts['S1 off']
        self.TrialParams['DelayLen'] = [np.round(v, 1) for v in
                                        self.TrialParams['DelayLenPrec']]

        # Init included trials (all trials included).
        self.TrialParams['included'] = np.array(True, dtype=bool)

        # %% Spikes.

        # Trials spikes, aligned to S1 onset.
        spk_trains = [(spk_train - abs_S1_onset[i]) * s
                      for i, spk_train in enumerate(TPLCell.TrialSpikes)]
        t_starts = self.ev_times('fixate')  # start of trial
        t_stops = self.ev_times('saccade')  # end of trial
        self.Spikes = Spikes(spk_trains, t_starts, t_stops)

        # %% Rates.

        # Estimate firing rate in each trial.
        spikes = self.Spikes.get_spikes()
        rate_list = [Rate(name, kernel, spikes, step)
                     for name, kernel in kernels.items()]
        self.Rates = pd.Series(rate_list, index=kernels.keys())

        # %% Unit params.

        self.UnitParams['empty'] = False
        self.UnitParams['excluded'] = False

    # %% Utility methods.

    def is_empty(self):
        """Return 1 if unit is empty, 0 if not empty."""

        im_empty = self.UnitParams['empty']
        return im_empty

    def is_excluded(self):
        """Return 1 if unit is excluded, 0 if included."""

        im_excluded = self.UnitParams['excluded']
        return im_excluded

    def get_region(self):
        """Return unit's region of origin."""

        my_region = self.UnitParams['region']
        return my_region

    def set_excluded(self, to_excl):
        """Set unit's exclude flag."""

        self.UnitParams['excluded'] = to_excl

    def set_name(self, name=None):
        """Set/update unit's name."""

        self.Name = name
        if name is None:  # set name to session, channel and unit parameters
            params = self.SessParams[['task', 'recording', 'probe',
                                      'channel #', 'unit #', 'sort #']]
            task, rec, probe, chan, un, isort = params
            subj, date = rec.split('_')
            self.Name = (' '.join([task, subj, date, probe]) +
                         ' Ch{:02}/{} ({})'.format(chan, un, isort))

    def name_to_fname(self):
        """Return filename compatible name string."""

        fname = util.format_to_fname(self.Name)
        return fname

    def add_index_to_name(self, i):
        """Add index to unit's name."""

        idx_str = 'Unit {:0>3}  '.format(i)

        # Remove previous index, if present.
        if self.Name[:5] == 'Unit ':
            self.Name = self.Name[len(idx_str):]

        self.Name = idx_str + self.Name

    def get_uid(self):
        """Return (recording, channel #, unit #) index triple."""

        uid = self.SessParams[['recording', 'channel #', 'unit #']]
        return uid

    def get_utid(self):
        """Return (recording, channel #, unit #, task) index quadruple."""

        utid = self.SessParams[['recording', 'channel #', 'unit #', 'task']]
        return utid

    def get_unit_params(self, rem_dims=True):
        """Return main unit parameters."""

        upars = pd.Series()

        # Basic params.
        upars['Name'] = self.Name
        upars['region'] = self.get_region()
        upars['excluded'] = self.is_excluded()

        # Recording params.
        upars['Session information'] = ''
        upars = upars.append(util.get_scalar_vals(self.SessParams, rem_dims))

        # Quality metrics.
        upars['Quality metrics'] = ''
        upars = upars.append(util.get_scalar_vals(self.QualityMetrics,
                                                  rem_dims))

        # Direction selectivity.
        upars['DS'] = ''
        for pname, pdf in self.DS.items():
            upars[pname] = ''
            pdf_melt = pd.melt(pdf)
            idxs = list(pdf.index)
            if isinstance(pdf.index, pd.MultiIndex):
                idxs = [' '.join(idx) for idx in idxs]
            stim_list = pdf.shape[1] * idxs
            pdf_melt.index = [' '.join([pr, st]) for pr, st
                              in zip(pdf_melt.variable, stim_list)]
            upars = upars.append(util.get_scalar_vals(pdf_melt.value,
                                                      rem_dims))

        return upars

    def update_included_trials(self, tr_inc):
        """Update fields related to included/excluded trials and spikes."""

        # Init included and excluded trials.
        tr_inc = np.array(tr_inc, dtype=bool)
        tr_exc = np.invert(tr_inc)

        # Update included trials.
        self.TrialParams['included'] = tr_inc

        # Statistics on trial inclusion.
        self.QualityMetrics['NTrialsTotal'] = len(self.TrialParams.index)
        self.QualityMetrics['NTrialsInc'] = np.sum(tr_inc)
        self.QualityMetrics['NTrialsExc'] = np.sum(tr_exc)

        # Update included spikes.
        if tr_inc.all():  # all trials included: include full recording
            t1 = self.SpikeParams['time'].min()
            t2 = self.SpikeParams['time'].max()
        else:
            # Include spikes occurring between first and last included trials.
            t1 = self.TrialParams.loc[tr_inc, 'TrialStart'].min()
            t2 = self.TrialParams.loc[tr_inc, 'TrialStop'].max()

        spk_inc = util.indices_in_window(self.SpikeParams['time'], t1, t2)
        self.SpikeParams['included'] = spk_inc

    def get_trial_params(self, trs=None, t1s=None, t2s=None):
        """Return default values of trials, start times and stop times."""

        if trs is None:
            trs = self.inc_trials()       # default: all included trials
        if t1s is None:
            t1s = self.ev_times('fixate')   # default: start of fixation
        if t2s is None:
            t2s = self.ev_times('saccade')  # default: saccade time

        return trs, t1s, t2s

    def pref_dir(self, stim='S1', method='weighted', pd_type='cPD'):
        """Return preferred direction."""

        pdir = self.DS['PD'].loc[(stim, method), pd_type]
        return pdir

    def anti_pref_dir(self, stim='S1', method='weighted', pd_type='cAD'):
        """Return anti-preferred direction."""

        adir = self.DS['PD'].loc[(stim, method), pd_type]
        return adir

    def init_nrate(self, nrate=None):
        """Initialize rate name."""

        def_nrate = constants.def_nrate

        if nrate is None:
            nrate = (def_nrate if def_nrate in self.Rates
                     else self.Rates.index[0])

        elif nrate not in self.Rates:
            warnings.warn('Rate name: ' + str(nrate) + ' not found in unit.')
            self.init_nrate()  # return default (or first available) rate name

        return nrate

    # %% Methods to get times of trial events and periods.

    def ev_times(self, evname):
        """Return timing of events across trials."""

        evt = self.Events[evname]
        return evt

    def pr_times(self, prname):
        """Return timing of period (start event, stop event) across trials."""

        ev1, ev2 = constants.tr_prd.loc[prname]
        evt1, evt2 = self.ev_times(ev1), self.ev_times(ev2)
        prt = pd.concat([evt1, evt2], axis=1)

        return prt

    # %% Generic methods to get various set of trials.

    def inc_trials(self):
        """Return included trials (i.e. not rejected after quality test)."""

        inc_trs = self.TrialParams['included']
        return inc_trs

    def filter_trials(self, trs):
        """Filter out excluded trials."""

        trs = trs & self.inc_trials()
        return trs

    def correct_incorrect_trials(self):
        """Return indices of correct and incorrect trials."""

        corr = self.Answer['AnswCorr']
        ctrs = pd.DataFrame()
        ctrs['correct'] = corr
        ctrs['error'] = ~corr

        return ctrs

    def pvals_in_trials(self, trs=None, pnames=None):
        """Return selected stimulus params during given trials."""

        # Defaults.
        if trs is None:  # all trials
            trs = np.ones(len(self.StimParams.index), dtype=bool)
        if pnames is None:  # all stimulus params
            pnames = self.StimParams.columns.values

        pvals = self.StimParams.loc[trs, pnames]

        return pvals

    def trials_by_pvals(self, stim, feat, vals=None, comb_values=False):
        """Return trials grouped by (selected) values of stimulus param."""

        # Group indices by stimulus feature value.
        tr_grps = self.StimParams.groupby([(stim, feat)]).groups

        # Default: all values of stimulus feature.
        if vals is None:
            vals = sorted(tr_grps.keys())

        # Convert to Series of trial list per feature value.
        tr_grps = util.series_from_tuple_list([(v, np.array(tr_grps[v]))
                                               for v in vals])

        # Optionally, combine trials across feature values.
        if comb_values:
            tr_grps = util.aggregate_lists(tr_grps)

        return tr_grps

    # %% Methods that provide interface to Unit's Spikes data.

    def get_prd_rates(self, trs=None, t1s=None, t2s=None, tr_time_idx=False):
        """Return rates within time periods in given trials."""

        if self.is_empty():
            return None

        # Init trials.
        trs, t1s, t2s = self.get_trial_params(trs, t1s, t2s)

        # Get rates.
        rates = self.Spikes.rates(trs, t1s, t2s)

        # Change index from trial index to trials start times.
        if tr_time_idx:
            tr_time = self.TrialParams.loc[trs, 'TrialStart']
            rates.index = util.remove_dim_from_series(tr_time)

        return rates

    # %% Methods to get trials with specific stimulus directions.

    def dir_trials(self, direc, stims=['S1', 'S2'], offsets=[0*deg],
                   comb_trs=False):
        """Return trials with some direction +- offset during S1 and/or S2."""

        # Init list of directions.
        direcs = [float(util.deg_mod(direc + offset)) for offset in offsets]

        # Get trials for direction + each offset value.
        sd_trs = [((stim, d), self.trials_by_pvals(stim, 'Dir', [d]).loc[d])
                  for d in direcs for stim in stims]
        sd_trs = util.series_from_tuple_list(sd_trs)

        # Combine values across trials.
        if comb_trs:
            sd_trs = util.combine_lists(sd_trs)

        return sd_trs

    def dir_pref_trials(self, pref_of, **kwargs):
        """Return trials with preferred direction."""

        pdir = self.pref_dir(pref_of)
        trs = self.dir_trials(pdir, **kwargs)

        return trs

    def dir_anti_trials(self, anti_of, **kwargs):
        """Return trials with anti-preferred direction."""

        adir = self.anti_pref_dir(anti_of)
        trs = self.dir_trials(adir, **kwargs)

        return trs

    def dir_pref_anti_trials(self, pref_anti_of, **kwargs):
        """Return trials with preferred and antipreferred direction."""

        pref_trials = self.dir_pref_trials(pref_anti_of, **kwargs)
        apref_trials = self.dir_anti_trials(pref_anti_of, **kwargs)
        pref_apref_trials = pref_trials.append(apref_trials)

        return pref_apref_trials

    def S_D_trials(self, pref_of, offsets=[0*deg]):
        """
        Return trials for S1 = S2 (same) and S1 =/= S2 (different)
        with S2 being at given offset from the unit's preferred direction.
        """

        # Collect S- and D-trials for all offsets.
        trS, trD = pd.Series(), pd.Series()
        for offset in offsets:

            # Trials to given offset to preferred direction.
            # stims order must be first S2, then S1!
            trs = self.dir_pref_trials(pref_of, stims=['S2', 'S1'], [offset])

            # S- and D-trials.
            trS[float(offset)] = util.intersect_lists(trs)[0]
            trD[float(offset)] = util.diff_lists(trs)[0]

        # Combine S- and D-trials across offsets.
        trS = util.union_lists(trS, 'S trials')
        trD = util.union_lists(trD, 'D trials')

        trsSD = trS.append(trD)

        return trsSD

    # %% Methods to calculate tuning curves and preferred values of features.

    def calc_response_stats(self, pname, t1s, t2s):
        """Calculate mean response to different values of trial parameter."""

        # Get trials for each parameter value.
        trs = self.trials_by_param_values(pname)

        # Calculate spike count and stats for each value of parameter.
        par_vals = [float(tr.value) for tr in trs]
        sp_stats = pd.DataFrame([self.Spikes.spike_rate_stats(tr, t1s, t2s)
                                 for tr in trs], index=par_vals)

        return sp_stats

    def calc_dir_response(self, stim, t1=None, t2=None):
        """Calculate mean response to each direction during given stimulus."""

        # Init stimulus.
        pname = stim + 'Dir'

        # Init time period.
        t1_stim, t2_stim = constants.del_stim_prds.periods(stim)
        if t1 is None:
            t1 = t1_stim
        if t2 is None:
            t2 = t2_stim

        # Calculate response statistics.
        response_stats = self.calc_response_stats(pname, t1, t2)

        return response_stats

    def calc_DS(self, stim, t1=None, t2=None):
        """Calculate direction selectivity (DS)."""

        pd_idx = ['PD', 'cPD', 'AD', 'cAD']

        # Get response stats to each direction.
        resp_stats = self.calc_dir_response(stim, t1, t2)
        dirs = np.array(resp_stats.index) * deg
        meanFR, stdFR, semFR = [util.dim_series_to_array(resp_stats[stat])
                                for stat in ('mean', 'std', 'sem')]

        # DS based on maximum rate only (legacy method).
        mPD = dirs[np.argmax(meanFR)]
        mAD = util.deg_mod(mPD+180*deg)
        cmPD, cmAD = mPD, mAD
        mPR, mAR = [meanFR[np.where(dirs == d)[0]] for d in (mPD, mAD)]
        mDS = float(util.modulation_index(mPR, mAR)) if mAR.size else np.nan

        mPDres = pd.Series([mPD, mAD, cmPD, cmAD], pd_idx)

        # DS based on weighted sum of all rates & directions.
        wDS, wPD, cwPD = util.deg_w_mean(dirs, meanFR, constants.all_dirs)
        wAD, cwAD = [util.deg_mod(d+180*deg) for d in (wPD, cwPD)]

        wPDres = pd.Series([wPD, cwPD, wAD, cwAD], pd_idx)

        # Calculate parameters of Gaussian tuning curve.
        # Start by centering stimulus - response.
        tun_res = tuning.center_pref_dir(dirs, wPD, meanFR, semFR)
        dirs_cntr, meanFR_cntr, semFR_cntr = tun_res
        # Fit Gaussian tuning curve to stimulus - response.
        fit_params, fit_res = tuning.fit_gaus_curve(dirs_cntr, meanFR_cntr,
                                                    semFR_cntr)

        # DS based on Gaussian tuning curve fit.
        tPD = wPD + fit_params.loc['fit', 'x0']
        ctPD = util.coarse_dir(tPD, constants.all_dirs)
        tAD, ctAD = [util.deg_mod(d+180*deg) for d in (tPD, ctPD)]

        tPDres = pd.Series([tPD, ctPD, tAD, ctAD], pd_idx)

        PD = pd.concat([mPDres, wPDres, tPDres], axis=1,
                       keys=('max', 'weighted', 'tuned'))
        DSI = pd.Series([mDS, wDS], index=['mDS', 'wDS'])

        # Prepare results.
        res = {'dirs': dirs, 'meanFR': meanFR, 'stdFR': stdFR, 'semFR': semFR,
               'dirs_cntr': dirs_cntr, 'meanFR_cntr': meanFR_cntr,
               'semFR_cntr': semFR_cntr, 'fit_params': fit_params,
               'fit_res': fit_res, 'PD': PD, 'DSI': DSI}

        return res

    def test_DS(self, stims=['S1', 'S2'], no_labels=False, do_plot=True,
                ftempl=None, **kwargs):
        """
        Test DS of unit by calculating
          - DS index and PD, and
          - parameters of Gaussian tuning curve.
        """

        # Init field to store DS results.
        DSres_plot = {}
        lDSI, lPD, lTP = [], [], []
        for stim in stims:

            res = self.calc_DS(stim, t1=None, t2=None)

            # Generate data points for plotting fitted tuning curve.
            a, b, x0, sigma = res['fit_params'].loc['fit']
            x, y = tuning.gen_fit_curve(tuning.gaus, deg, -180*deg, 180*deg,
                                        a=a, b=b, x0=x0, sigma=sigma)

            # Collect calculated DS results param values.
            lDSI.append(res['DSI'])
            lPD.append(res['PD'])

            # TPs
            lTP.append(res['fit_params'].loc['fit'].append(res['fit_res']))

            # Collect data for plotting.
            DSres_plot[stim] = res
            DSres_plot[stim]['xfit'] = x
            DSres_plot[stim]['yfit'] = y

        # Convert each to a DataFrame.
        DSI, PD, TP = [pd.concat(rlist, axis=1, keys=stims).T
                       for rlist in (lDSI, lPD, lTP)]

        # Save DS results.
        self.DS['DSI'] = DSI
        self.DS['PD'] = PD
        self.DS['TP'] = TP

        # Plot direction selectivity results.
        if do_plot:
            DSres_plot = pd.DataFrame(DSres_plot).T
            title = self.Name

            # Minimise labels on plot.
            if no_labels:
                title = None
                kwargs['labels'] = False
                kwargs['polar_legend'] = True
                kwargs['tuning_legend'] = False

            ffig = (None if ftempl is None
                    else ftempl.format(self.name_to_fname()))
            ptuning.direction_selectivity(DSres_plot, title=title,
                                          ffig=ffig, **kwargs)

    # %% Plotting methods.

    def prep_plot_params(self, nrate, trs, t1s, t2s):
        """Prepare plotting parameters."""

        # Get trial params.
        trs, t1s, t2s = self.get_trial_params(trs, t1s, t2s)
        names = [tr.name for tr in trs]

        # Get spikes.
        spikes = [self.Spikes.get_spikes(tr, t1s, t2s) for tr in trs]

        # Get rates and rate times.
        nrate = self.init_nrate(nrate)
        rates, time = None, None
        if nrate is not None:
            rates = [self.Rates[nrate].get_rates(tr.trials, t1s, t2s)
                     for tr in trs]
            time = self.Rates[nrate].get_sample_times(t1s, t2s)

        return trs, t1s, t2s, spikes, rates, time, names

    def plot_raster(self, nrate=None, trs=None, t1=None, t2=None, **kwargs):
        """Plot raster plot of unit for specific trials."""

        if self.is_empty:
            return

        # Set up params.
        plot_params = self.prep_plot_params(nrate, trs, t1, t2)
        trs, t1, t2, spikes, rates, tvec, names = plot_params
        spikes = spikes[0]
        names = names[0]

        # Plot raster.
        ax = prate.raster(spikes, t1, t2, prds=constants.stim_prds,
                          title=self.Name, **kwargs)

        return ax

    def plot_rate(self, nrate=None, trs=None, t1=None, t2=None, **kwargs):
        """Plot rate plot of unit for specific trials."""

        if self.is_empty:
            return

        # Set up params.
        plot_params = self.prep_plot_params(nrate, trs, t1, t2)
        trs, t1, t2, spikes, rates, tvec, names = plot_params

        # Plot rate.
        ax = prate.rate(rates, tvec, names, t1, t2, prds=constants.stim_prds,
                        title=self.Name, **kwargs)

        return ax

    def plot_raster_rate(self, nrate=None, trs=None, t1=None, t2=None,
                         no_labels=False, rate_kws=dict(), **kwargs):
        """Plot raster and rate plot of unit for specific trials."""

        if self.is_empty:
            return

        # Set up params.
        plot_params = self.prep_plot_params(nrate, trs, t1, t2)
        trs, t1, t2, spikes, rates, tvec, names = plot_params

        # Set labels.
        title = self.Name if not no_labels else None
        if no_labels:
            rate_kws.update({'xlab': None, 'ylab': None, 'add_lgn': False})

        # Plot raster and rate.
        res = prate.raster_rate(spikes, rates, tvec, names, t1, t2,
                                prds=constants.stim_prds, title=title,
                                rate_kws=rate_kws, **kwargs)
        fig, raster_axs, rate_ax = res

        return fig, raster_axs, rate_ax

    def plot_dir_resp(self):
        """Plot response to all directions + polar plot in center."""

        # TODO: to be moved here from quality. Along with RR/DS summary plot!
        pass
