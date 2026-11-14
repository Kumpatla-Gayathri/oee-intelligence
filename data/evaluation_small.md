```
Machine-shifts: 126   flagged: 20

DATA-QUALITY anomalies vs ground truth [EVALUATED]
  labels=15 predictions=15 detected=15 missed=0 false_positives=0
  precision=1.000 recall=1.000 f1=1.000 FPR=0.000 type_coverage=1.000
    COUNT_DECREASE               P=1.000 R=1.000 F1=1.000 FN=0 FP=0
    DOWNTIME_IN_PLANNED_BREAK    P=1.000 R=1.000 F1=1.000 FN=0 FP=0
    OVERLAPPING_STATES           P=1.000 R=1.000 F1=1.000 FN=0 FP=0
    SCRAP_EXCEEDS_TOTAL          P=1.000 R=1.000 F1=1.000 FN=0 FP=0
    WRONG_CYCLE_TIME             P=1.000 R=1.000 F1=1.000 FN=0 FP=0

OPERATIONAL alerts [LABELS_UNAVAILABLE]  total=5 by_type={'AVAILABILITY_DROP': 4, 'PERFORMANCE_DROP': 1}
  No reliable operational ground-truth labels exist (the generator only injects data-quality issues). Operational precision, recall, F1 and false-positive rate cannot be calculated. Supply --operational-labels to enable them.
```
