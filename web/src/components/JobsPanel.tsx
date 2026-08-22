import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { describeJob, everyLabel, isoAgo, jobSentence } from "../lib/automation";
import type { Job, JobList, JobRun, JobSpec, VaultMeta } from "../types";
import JobForm, { type JobTarget } from "./JobForm";

const EMPTY: JobList = { jobs: [], suggested: [], kinds: [], connectors: [] };

/** How the last run went, in the two colours that mean it. */
function LastRun({ job }: { job: Job }) {
  if (!job.last_run) {
    return <span className="muted">never run</span>;
  }
  const ok = job.last_status === "ok";
  return (
    <>
      <span className="muted">{isoAgo(job.last_run)}</span>
      <span className={ok ? "run-ok" : "run-fail"}>
        {ok ? "✓" : "✗"} {job.last_detail || job.last_status}
      </span>
    </>
  );
}

function JobRow({
  job,
  result,
  running,
  onToggle,
  onRun,
  onEdit,
  onDelete,
}: {
  job: Job;
  result: JobRun | null;
  running: boolean;
  onToggle: (job: Job, enabled: boolean) => void;
  onRun: (job: Job) => void;
  onEdit: (job: Job) => void;
  onDelete: (job: Job) => void;
}) {
  return (
    <div className="auto-row">
      <div className="auto-row-head">
        <p className={job.enabled ? "auto-sentence" : "auto-sentence auto-off"}>
          {jobSentence(job)}
        </p>
        <div className="auto-row-actions">
          <label className="toggle">
            <input
              type="checkbox"
              checked={job.enabled}
              onChange={(e) => onToggle(job, e.target.checked)}
            />
            <span>Enabled</span>
          </label>
          <button className="btn btn-sm" onClick={() => onRun(job)} disabled={running}>
            {running ? "Running…" : "Run now"}
          </button>
          <button className="btn btn-sm" onClick={() => onEdit(job)}>
            Edit
          </button>
          <button className="btn btn-sm danger" onClick={() => onDelete(job)}>
            Delete
          </button>
        </div>
      </div>
      <p className="auto-row-meta">
        <span className="mono">{job.name}</span>
        <span className="muted"> · {everyLabel(job.interval_hours)} · </span>
        <LastRun job={job} />
      </p>
      {result && (
        <p className={result.status === "ok" ? "run-ok auto-ran" : "run-fail auto-ran"}>
          {result.status === "ok" ? "✓" : "✗"} ran just now — {result.detail || result.status}
        </p>
      )}
    </div>
  );
}

export default function JobsPanel({ active }: { active: boolean }) {
  const [list, setList] = useState<JobList>(EMPTY);
  const [vaults, setVaults] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, JobRun>>({});
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<JobTarget | null>(null);

  const load = useCallback(() => {
    apiGet<JobList>("/api/jobs")
      .then((r) => setList({ ...EMPTY, ...r }))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load jobs"));
  }, []);

  useEffect(() => {
    if (!active) return;
    load();
    apiGet<{ vaults: VaultMeta[] }>("/api/vaults")
      .then((r) => setVaults(r.vaults.map((v) => v.name)))
      .catch(() => setVaults(["shared"]));
  }, [active, load]);

  const save = async (job: JobSpec) => {
    setError(null);
    try {
      await apiSend("PUT", "/api/jobs", { job });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save that job");
    }
  };

  const run = async (job: Job) => {
    setRunning(job.name);
    setError(null);
    try {
      const result = await apiSend<JobRun>(
        "POST",
        `/api/jobs/${encodeURIComponent(job.name)}/run`,
      );
      setResults((r) => ({ ...r, [job.name]: result }));
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "the job could not be run");
    } finally {
      setRunning(null);
    }
  };

  const remove = async (job: Job) => {
    if (!window.confirm(`Delete the job "${job.name}"? It stops running; nothing it already did is undone.`)) {
      return;
    }
    setError(null);
    try {
      await apiSend("DELETE", `/api/jobs/${encodeURIComponent(job.name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
    load();
  };

  const known = new Set(list.jobs.map((j) => j.name));
  const offers = list.suggested.filter((s) => !known.has(s.name));

  return (
    <section className="card auto-panel">
      <div className="auto-panel-head">
        <h3>Scheduled jobs</h3>
        <button
          className="btn btn-sm"
          onClick={() => setTarget({ job: null, nonce: Date.now() })}
        >
          + New job
        </button>
      </div>
      <p className="auto-blurb">
        Jobs are the clock. Each one repeats on an interval — sync a connector, re-index,
        run the rules, write a digest — and reports how it went.
      </p>

      {error && (
        <div className="banner banner-error">
          <span>✗ {error}</span>
          <button className="btn btn-sm" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {list.jobs.length === 0 ? (
        <p className="muted auto-none">
          Nothing scheduled. Add one of the ready-made jobs below, or make your own.
        </p>
      ) : (
        <div className="auto-rows">
          {list.jobs.map((job) => (
            <JobRow
              key={job.name}
              job={job}
              result={results[job.name] ?? null}
              running={running === job.name}
              onToggle={(j, enabled) => void save({ ...j, enabled })}
              onRun={(j) => void run(j)}
              onEdit={(j) => setTarget({ job: j, nonce: Date.now() })}
              onDelete={(j) => void remove(j)}
            />
          ))}
        </div>
      )}

      {offers.length > 0 && (
        <div className="auto-suggested">
          <p className="label">Ready-made jobs</p>
          <p className="auto-blurb">
            Added switched off. Turn one on, or press Run now to see what it does first.
          </p>
          <div className="auto-chips">
            {offers.map((s) => (
              <button
                key={s.name}
                className="auto-chip"
                onClick={() => void save({ ...s, enabled: false })}
                title={`Add the job "${s.name}"`}
              >
                <span className="auto-chip-add">Add</span>
                <span className="auto-chip-text">{describeJob(s)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {target && (
        <JobForm
          key={target.nonce}
          target={target}
          kinds={list.kinds}
          connectors={list.connectors}
          vaults={vaults}
          onClose={() => setTarget(null)}
          onSaved={() => {
            setTarget(null);
            load();
          }}
        />
      )}
    </section>
  );
}
