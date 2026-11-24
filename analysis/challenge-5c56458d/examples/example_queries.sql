-- =============================================================================
-- Safe Scientific Cross-Table SQL Patterns (Illustrative Examples Only)
-- =============================================================================

-- Pattern 1 (SAFE): Pre-aggregate 1:N child table (hplc_injections) to 1 row per
-- sample_code BEFORE joining to wine_samples and LEFT JOINing from cultivars so
-- that (a) sample counts stay exact (no 1:N inflation) and (b) legitimate empty
-- dimension groups (0 samples) remain visible with NULL averages.
WITH hplc_per_sample AS (
    SELECT
        sample_code,
        COUNT(*) AS n_injections,
        SUM(qc_pass) AS n_qc_pass,
        AVG(CAST(qc_pass AS REAL)) AS sample_qc_pass_rate,
        AVG(retention_drift_s) AS mean_retention_drift_s
    FROM hplc_injections
    GROUP BY sample_code
)
SELECT
    c.cultivar_code,
    c.cultivar_label,
    COUNT(w.sample_code) AS n_samples,
    AVG(w.alcohol_pct) AS mean_alcohol_pct,
    COALESCE(SUM(h.n_injections), 0) AS total_injections,
    AVG(h.sample_qc_pass_rate) AS mean_sample_qc_pass_rate
FROM cultivars c
LEFT JOIN wine_samples w
    ON c.cultivar_code = w.cultivar_code
LEFT JOIN hplc_per_sample h
    ON w.sample_code = h.sample_code
GROUP BY c.cultivar_code, c.cultivar_label
ORDER BY c.cultivar_code;

-- Pattern 2 (SAFE): Explicit orphan foreign-key audit query (set allow_empty_result=true).
SELECT h.injection_id, h.sample_code
FROM hplc_injections h
LEFT JOIN wine_samples w
    ON h.sample_code = w.sample_code
WHERE w.sample_code IS NULL;
