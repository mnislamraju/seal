# -*- coding: utf-8 -*-
"""
Functions related to exporting data.

@author: David Samu
"""


import numpy as np
import pandas as pd

from seal.util import util, constants


def export_unit_list(UA, fname):
    """Export unit list and parameters into Excel table."""

    unit_params = UA.unit_params()
    writer = pd.ExcelWriter(fname)
    util.write_table(unit_params, writer)


def export_unit_trial_selection(UA, fname):
    """Export unit and trial selection as Excel table."""

    # Gather selection dataframe.
    dselect = {}
    for i, u in enumerate(UA.iter_thru(excl=True)):
        iselect = u.get_utid()
        iselect['unit included'] = int(not u.is_excluded())
        inc_trs = u.inc_trials()
        ftr, ltr = 0, 0
        if len(inc_trs):
            ftr, ltr = inc_trs.min()+1, inc_trs.max()+1
        iselect['first included trial'] = ftr
        iselect['last included trial'] = ltr
        dselect[i] = iselect

    # Sort table to help reading by recording.
    SelectDF = pd.concat(dselect, axis=1).T
    SelectDF.sort_values(constants.utid_names, inplace=True)
    SelectDF.index = range(1, len(SelectDF.index)+1)

    # Write out selection dataframe.
    writer = pd.ExcelWriter(fname)
    util.write_table(SelectDF, writer)


def export_decoding_data(UA, fname, rec, task, trs, uids, prd, nrate):
    """Export decoding data into .mat file."""

    # Below inits rely on these params being the same across units, which is
    # only true when exporting a single task of a single recording!

    if uids is None:
        uids = UA.uids([task])[rec]

    u = UA.get_unit(uids[0], task)
    t1s, t2s = u.pr_times(prd, trs, add_latency=False, concat=False)
    prd_str = constants.tr_prds.loc[prd, 'start']
    ref_ev = constants.tr_evts.loc[prd_str, 'rel to']
    ref_ts = u.ev_times(ref_ev)
    if nrate is None:
        nrate = u.init_nrate()

    # Trial params.
    trpars = np.array([util.remove_dim_from_series(u.TrData[par][trs])
                       for par in u.TrData]).T
    trpar_names = ['_'.join(col) if util.is_iterable(col) else col
                   for col in u.TrData.columns]

    # Trial events.
    tr_evts = u.Events
    trevn_names = tr_evts.columns.tolist()
    tr_evts = np.array([util.remove_dim_from_series(tr_evts.loc[trs, evn])
                       for evn in tr_evts]).T

    # Rates.
    rates = np.array([np.array(u._Rates[nrate].get_rates(trs, t1s, t2s))
                      for u in UA.iter_thru([task], uids)])

    # Sampling times.
    times = np.array(u._Rates[nrate].get_rates(trs, t1s, t2s, ref_ts).columns)

    # Create dictionary to export.
    export_dict = {'recording': rec, 'task': task,
                   'period': prd, 'nrate': nrate,
                   'trial_parameter_names': trpar_names,
                   'trial_parameters': trpars,
                   'trial_event_names': trevn_names,
                   'trial_events': tr_evts,
                   'times': times, 'rates': rates}

    # Export data.
    util.write_matlab_object(fname, export_dict)
