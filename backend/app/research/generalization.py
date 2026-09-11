from statistics import mean


LABELS = {"confirmed": "CONFIRMED", "weakened": "WEAKENED", "reversed": "REVERSED",
          "insufficient": "INSUFFICIENT SAMPLE"}


def _average(rows, horizon="32"):
    return mean(row["forward_returns"][horizon] for row in rows) if rows else None


def _directional(rows, predicate, expected_positive=True, minimum=5):
    sample=[row for row in rows if predicate(row)]
    if len(sample)<minimum:return {"label":LABELS["insufficient"],"n":len(sample),"mean_1d":None}
    value=_average(sample);supports=value>0 if expected_positive else value<=0
    return {"label":LABELS["confirmed"] if supports else LABELS["reversed"],"n":len(sample),"mean_1d":round(value,6)}


def evaluate_v3_findings(rows, minimum=5):
    low=[row for row in rows if row["score"]<70];high=[row for row in rows if row["score"]>=70]
    if len(low)<minimum or len(high)<minimum:
        ranking={"label":LABELS["insufficient"],"n_low":len(low),"n_high":len(high)}
    else:
        comparisons=[_average(high,h)>_average(low,h) for h in ("16","32")]
        label=LABELS["confirmed"] if all(comparisons) else LABELS["weakened"] if any(comparisons) else LABELS["reversed"]
        ranking={"label":label,"n_low":len(low),"n_high":len(high),"high_minus_low_4h":round(_average(high,"16")-_average(low,"16"),6),
                 "high_minus_low_1d":round(_average(high)-_average(low),6)}
    by_setup={name:[row for row in rows if row.get("setup_detected") and row.get("setup")==name]
              for name in ("BREAKOUT","PULLBACK","SUPPORT_BOUNCE","TREND_CONTINUATION")}
    continuation=by_setup["TREND_CONTINUATION"]
    eligible={name:_average(group) for name,group in by_setup.items() if len(group)>=minimum}
    if len(continuation)<minimum or len(eligible)<2:
        continuation_result={"label":LABELS["insufficient"],"n":len(continuation)}
    else:
        best=max(eligible,key=eligible.get)
        continuation_result={"label":LABELS["confirmed"] if best=="TREND_CONTINUATION" else LABELS["reversed"],
                             "n":len(continuation),"strongest_setup":best}
    mid=[row for row in rows if 1.5<=row.get("rvol",0)<2];high_rvol=[row for row in rows if row.get("rvol",0)>=2]
    if len(mid)<minimum or len(high_rvol)<minimum:
        rvol={"label":LABELS["insufficient"],"n_1_5_to_2":len(mid),"n_2_plus":len(high_rvol)}
    else:
        delta=_average(mid,"16")-_average(high_rvol,"16")
        rvol={"label":LABELS["confirmed"] if delta>0 else LABELS["reversed"],"n_1_5_to_2":len(mid),"n_2_plus":len(high_rvol),
              "mean_4h_delta":round(delta,6)}
    scored=[row for row in rows if row.get("setup_detected") and row["score"]>=82]
    rr_pass=[row for row in scored if row.get("rr",0)>=1.5];rr_fail=[row for row in scored if row.get("rr",0)<1.5]
    if len(rr_pass)<minimum or len(rr_fail)<minimum:
        rr={"label":LABELS["insufficient"],"n_pass":len(rr_pass),"n_rejected":len(rr_fail)}
    else:
        delta=_average(rr_fail,"16")-_average(rr_pass,"16")
        rr={"label":LABELS["confirmed"] if delta>=0 else LABELS["reversed"],"n_pass":len(rr_pass),"n_rejected":len(rr_fail),
            "rejected_minus_pass_4h":round(delta,6)}
    htf_yes=[row for row in rows if row.get("htf")];htf_no=[row for row in rows if not row.get("htf")]
    if len(htf_yes)<minimum or len(htf_no)<minimum:
        htf={"label":LABELS["insufficient"],"n_eligible":len(htf_yes),"n_ineligible":len(htf_no)}
    else:
        delta=_average(htf_yes,"16")-_average(htf_no,"16")
        htf={"label":LABELS["confirmed"] if delta>0 else LABELS["reversed"],"n_eligible":len(htf_yes),"n_ineligible":len(htf_no),
             "mean_4h_delta":round(delta,6)}
    return {"score_ranking":ranking,"breakout":_directional(rows,lambda row:row.get("setup_detected") and row.get("setup")=="BREAKOUT",True,minimum),
        "pullback":_directional(rows,lambda row:row.get("setup_detected") and row.get("setup")=="PULLBACK",True,minimum),
        "support_bounce":_directional(rows,lambda row:row.get("setup_detected") and row.get("setup")=="SUPPORT_BOUNCE",False,minimum),
        "trend_continuation":continuation_result,"rr":rr,"htf":htf,"rvol":rvol}
