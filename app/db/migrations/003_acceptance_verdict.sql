-- v3: persist the acceptance Verdict verbatim.
-- runs/attempts carry outcome + reason + failing_obligation + detail;
-- obligations carry the per-check CheckResult in result/detail.

ALTER TABLE attempts RENAME COLUMN reason_code TO reason;
ALTER TABLE attempts ADD COLUMN failing_obligation TEXT;
ALTER TABLE attempts ADD COLUMN detail TEXT;

ALTER TABLE runs ADD COLUMN reason TEXT;
ALTER TABLE runs ADD COLUMN failing_obligation TEXT;
ALTER TABLE runs ADD COLUMN detail TEXT;
