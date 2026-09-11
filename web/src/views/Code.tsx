import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { wsSubscribe } from "../ws";
import {
  CODE_JOB_KINDS,
  everyLabel,
  isoAgo,
  jobSentence,
  runWhen,
} from "../lib/automation";
import type {
  Job,
  JobList,
  JobRun,
  Repo,
  RepoList,
  RepoSync,
  Review,
} from "../types";
import RepoForm, { type RepoTarget } from "../components/RepoForm";
import ReviewForm, { type ReviewTarget } from "../components/ReviewForm";
import { Menu, MenuItem } from "../components/Menu";

const NO_REPOS: RepoList = {
  repos: [],
  providers: [],
  token_envs: {},
  env_path: "",
};
const NO_JOBS: JobList = {
  jobs: [],
  suggested: [],
  kinds: [],
  connectors: [],
  repos: [],
  review_modes: [],
  default_focus: "",
};

/**
 * One word about the row, in the colour that means it. State is a pill, not
 * a sentence: "synced 2h ago" reads at a glance where "just now ✓ already at
 * d04cf26a4d" had to be parsed.
 */
function RepoStatus({ repo, syncing }: { repo: Repo; syncing: boolean }) {
  if (syncing) return <span className="badge accent busy">syncing</span>;
  if (!repo.enabled) return <span className="badge">paused</span>;
  if (!repo.last_sync) return <span className="badge">never synced</span>;
  if (repo.last_status === "ok") {
    return <span className="badge up">synced {isoAgo(repo.last_sync)}</span>;
  }
  const auth = /authentication|token|private/i.test(repo.last_detail);
  return (
    <span className={auth ? "badge warn" : "badge down"}>
      {auth ? "needs a token" : "sync failed"}
    </span>
  );
}

function RunStatus({ job, running }: { job: Job; running: boolean }) {
  if (running) return <span className="badge accent busy">reviewing</span>;
  if (!job.enabled) return <span className="badge">paused</span>;
  if (!job.last_run) return <span className="badge">never run</span>;
  if (job.last_status === "ok")
    return <span className="badge up">ran {isoAgo(job.last_run)}</span>;
  return <span className="badge down">failed {isoAgo(job.last_run)}</span>;
}

function RepoRow({
  repo,
  isAdmin,
  syncing,
  result,
  onToggle,
  onSync,
  onEdit,
  onDelete,
}: {
  repo: Repo;
  isAdmin: boolean;
  syncing: boolean;
  result: RepoSync | null;
  onToggle: (repo: Repo, enabled: boolean) => void;
  onSync: (repo: Repo) => void;
  onEdit: (repo: Repo) => void;
  onDelete: (repo: Repo) => void;
}) {
  const failed = !syncing && repo.last_sync !== "" && repo.last_status !== "ok";
  return (
    <div className="auto-row">
      <div className="auto-row-head">
        <p
          className={repo.enabled ? "auto-sentence" : "auto-sentence auto-off"}
        >
          <span className="mono">{repo.name}</span>
          <a
            className="repo-link"
            href={repo.url}
            target="_blank"
            rel="noreferrer"
          >
            {repo.slug}
          </a>
          {repo.branch && <span className="muted"> · {repo.branch}</span>}
        </p>
        <div className="auto-row-actions">
          <RepoStatus repo={repo} syncing={syncing} />
          {isAdmin && (
            <>
              <button
                className="btn btn-sm"
                onClick={() => onSync(repo)}
                disabled={syncing || !repo.enabled}
              >
                {syncing ? "Syncing…" : "Sync now"}
              </button>
              <Menu label={`More actions for ${repo.name}`}>
                <MenuItem onClick={() => onEdit(repo)}>Edit</MenuItem>
                <MenuItem onClick={() => onToggle(repo, !repo.enabled)}>
                  {repo.enabled
                    ? "Pause — stop syncing and reviewing"
                    : "Resume"}
                </MenuItem>
                <MenuItem danger onClick={() => onDelete(repo)}>
                  Remove
                </MenuItem>
              </Menu>
            </>
          )}
        </div>
      </div>
      <p className="auto-row-meta">
        <span className="mono">{repo.prefix}/</span>
        <span className="muted">
          {" "}
          ·{" "}
          {repo.sync_hours > 0
            ? `refreshed ${everyLabel(repo.sync_hours)}`
            : "refreshed on request"}
        </span>
        {repo.head && (
          <span className="muted">
            {" "}
            · at <span className="mono">{repo.head.slice(0, 7)}</span>
            {repo.head_subject ? ` “${repo.head_subject}”` : ""}
          </span>
        )}
      </p>
      {failed && <p className="auto-row-meta run-fail">{repo.last_detail}</p>}
      {failed && !repo.token_present && (
        <p className="auto-row-meta muted">
          No token is set in <span className="mono">{repo.token_env}</span>. A
          private repository needs one; put it in{" "}
          <span className="mono">.env</span> and sync again.
        </p>
      )}
      {result && result.status === "ok" && (
        <p className="run-ok auto-ran">✓ {result.detail || "synced"}</p>
      )}
    </div>
  );
}

function ReviewJobRow({
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
  const failed = !running && job.last_run !== "" && job.last_status !== "ok";
  const line = result
    ? result.detail || result.status
    : failed
      ? job.last_detail
      : "";
  const ok = result ? result.status === "ok" : !failed;
  return (
    <div className="auto-row">
      <div className="auto-row-head">
        <p className={job.enabled ? "auto-sentence" : "auto-sentence auto-off"}>
          {jobSentence(job)}
        </p>
        <div className="auto-row-actions">
          <RunStatus job={job} running={running} />
          <button
            className="btn btn-sm"
            onClick={() => onRun(job)}
            disabled={running}
          >
            {running ? "Reviewing…" : "Review now"}
          </button>
          <Menu label={`More actions for ${job.name}`}>
            <MenuItem onClick={() => onEdit(job)}>Edit</MenuItem>
            <MenuItem onClick={() => onToggle(job, !job.enabled)}>
              {job.enabled ? "Pause" : "Resume"}
            </MenuItem>
            <MenuItem danger onClick={() => onDelete(job)}>
              Delete
            </MenuItem>
          </Menu>
        </div>
      </div>
      <p className="auto-row-meta">
        <span className="muted">
          Looking for {String(job.settings.focus ?? "").slice(0, 90)}
        </span>
        {job.settings.channel ? (
          <span className="muted">
            {" "}
            · tells #{String(job.settings.channel)}
          </span>
        ) : null}
      </p>
      {line && (
        <p className={ok ? "run-ok auto-ran" : "run-fail auto-ran"}>{line}</p>
      )}
    </div>
  );
}

/** How many of each severity, as pills, only where there are any. */
function Severities({ r }: { r: Review }) {
  if (r.findings === 0) return <span className="badge">no findings</span>;
  return (
    <>
      {r.high > 0 && <span className="badge down">{r.high} high</span>}
      {r.medium > 0 && <span className="badge warn">{r.medium} medium</span>}
      {r.low > 0 && <span className="badge">{r.low} low</span>}
      {r.approved > 0 ? (
        <span className="badge up">{r.approved} approved</span>
      ) : (
        <span className="badge">{r.findings} waiting</span>
      )}
    </>
  );
}

/**
 * Repositories the brain reads, the reviews it is scheduled to write, and
 * the reviews it has written. Everyone sees all three; adding, syncing and
 * scheduling is admin work, because a repo is shared with the whole brain
 * the moment it is added.
 */
export default function Code({
  active,
  isAdmin,
  onVaultPath,
}: {
  active: boolean;
  isAdmin: boolean;
  onVaultPath: (path: string) => void;
}) {
  const [repos, setRepos] = useState<RepoList | null>(null);
  const [jobs, setJobs] = useState<JobList>(NO_JOBS);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncResults, setSyncResults] = useState<Record<string, RepoSync>>({});
  const [running, setRunning] = useState<string | null>(null);
  const [runResults, setRunResults] = useState<Record<string, JobRun>>({});
  const [error, setError] = useState<string | null>(null);
  const [repoTarget, setRepoTarget] = useState<RepoTarget | null>(null);
  const [reviewTarget, setReviewTarget] = useState<ReviewTarget | null>(null);

  const loadRepos = useCallback(() => {
    apiGet<RepoList>("/api/repos")
      .then((r) => setRepos({ ...NO_REPOS, ...r }))
      .catch((e) =>
        setError(
          e instanceof Error ? e.message : "failed to load repositories",
        ),
      );
  }, []);
  const loadJobs = useCallback(() => {
    if (!isAdmin) return;
    apiGet<JobList>("/api/jobs")
      .then((r) => setJobs({ ...NO_JOBS, ...r }))
      .catch((e) =>
        setError(e instanceof Error ? e.message : "failed to load reviews"),
      );
  }, [isAdmin]);
  const loadReviews = useCallback(() => {
    apiGet<{ reviews: Review[] }>("/api/reviews")
      .then((r) => setReviews(r.reviews ?? []))
      .catch(() => setReviews([]));
  }, []);
  const loadAll = useCallback(() => {
    loadRepos();
    loadJobs();
    loadReviews();
  }, [loadRepos, loadJobs, loadReviews]);

  useEffect(() => {
    if (active) loadAll();
  }, [active, loadAll]);

  // A sync finishing anywhere (the clock, another admin) updates the row;
  // a review landing in the vault updates the list.
  useEffect(
    () =>
      wsSubscribe((ev) => {
        if (ev.type === "repo_synced") loadRepos();
        if (
          ev.type === "vault_changed" &&
          ev.vault === "shared" &&
          ev.path.startsWith("reviews/")
        ) {
          loadReviews();
        }
      }),
    [loadRepos, loadReviews],
  );

  const syncRepo = async (repo: Repo) => {
    setSyncing(repo.name);
    setError(null);
    try {
      const result = await apiSend<RepoSync>(
        "POST",
        `/api/repos/${encodeURIComponent(repo.name)}/sync`,
      );
      setSyncResults((r) => ({ ...r, [repo.name]: result }));
      loadRepos();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "the sync could not be started",
      );
    } finally {
      setSyncing(null);
    }
  };

  const saveRepo = async (repo: Repo, patch: Partial<Repo>) => {
    setError(null);
    try {
      await apiSend("PUT", "/api/repos", { repo: { ...repo, ...patch } });
      loadRepos();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "could not save that repository",
      );
    }
  };

  const removeRepo = async (repo: Repo) => {
    if (
      !window.confirm(
        `Remove ${repo.name}? Its clone is deleted, it leaves the index, and any scheduled review of it is removed. Reviews already written stay in the vault.`,
      )
    ) {
      return;
    }
    setError(null);
    try {
      await apiSend("DELETE", `/api/repos/${encodeURIComponent(repo.name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "remove failed");
    }
    loadAll();
  };

  const saveJob = async (job: Job, patch: Partial<Job>) => {
    setError(null);
    try {
      await apiSend("PUT", "/api/jobs", { job: { ...job, ...patch } });
      loadJobs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not save that review");
    }
  };

  const runJob = async (job: Job) => {
    setRunning(job.name);
    setError(null);
    try {
      const result = await apiSend<JobRun>(
        "POST",
        `/api/jobs/${encodeURIComponent(job.name)}/run`,
      );
      setRunResults((r) => ({ ...r, [job.name]: result }));
      loadJobs();
      loadReviews();
      loadRepos();
    } catch (e) {
      setError(e instanceof Error ? e.message : "the review could not be run");
    } finally {
      setRunning(null);
    }
  };

  const removeJob = async (job: Job) => {
    if (
      !window.confirm(
        `Delete the scheduled review "${job.name}"? Reviews it wrote stay in the vault.`,
      )
    ) {
      return;
    }
    setError(null);
    try {
      await apiSend("DELETE", `/api/jobs/${encodeURIComponent(job.name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    }
    loadJobs();
  };

  const repoList = repos?.repos ?? [];
  const reviewJobs = jobs.jobs.filter((j) => CODE_JOB_KINDS.has(j.kind));
  const repoNames = repoList.filter((r) => r.enabled).map((r) => r.name);
  const loaded = repos !== null;
  const empty = loaded && repoList.length === 0;

  return (
    <div className="automation-view code-view">
      <header className="view-band">
        <div className="wrap auto-wrap">
          <div className="auto-head">
            <h2>Code</h2>
            <p className="auto-lead">
              Give the brain a repository to read and the agent can answer from
              your code and your notes together. Schedule a review and it reads
              what changed, with your notes open, and leaves findings for you to
              approve.
            </p>
          </div>
        </div>
      </header>
      <div className="view-scroll">
        <div className="wrap auto-wrap">
          {error && (
            <div className="banner banner-error">
              <span>✗ {error}</span>
              <button className="btn btn-sm" onClick={() => setError(null)}>
                Dismiss
              </button>
            </div>
          )}

          {empty && isAdmin && (
            <div className="start-here code-start">
              <div className="grid three start-grid">
                <div className="card start-card">
                  <p className="label">Step 1</p>
                  <h3>Add a repository</h3>
                  <p>
                    GitHub or GitLab, by{" "}
                    <span className="mono">owner/name</span>. A private one
                    needs a token in <span className="mono">.env</span>.
                  </p>
                  <button
                    className="btn primary"
                    onClick={() =>
                      setRepoTarget({ repo: null, nonce: Date.now() })
                    }
                  >
                    Add a repository
                  </button>
                </div>
                <div className="card start-card">
                  <p className="label">Step 2</p>
                  <h3>Schedule a review</h3>
                  <p>
                    Pick the repo, how often, and what to look for in your own
                    words. The first run looks at the whole codebase; after
                    that, only what changed.
                  </p>
                  <button
                    className="btn"
                    disabled
                    title="Add a repository first"
                  >
                    Schedule a review
                  </button>
                </div>
                <div className="card start-card">
                  <p className="label">Step 3</p>
                  <h3>Approve what it finds</h3>
                  <p>
                    Each review is a note in the shared vault. Tick a finding to
                    approve it for automated work; nothing acts on an unticked
                    one.
                  </p>
                </div>
              </div>
            </div>
          )}

          {empty && !isAdmin && (
            <p className="muted auto-none">
              No repositories yet — an admin adds them here.
            </p>
          )}

          {!empty && (
            <section className="card auto-panel">
              <div className="auto-panel-head">
                <h3>Repositories</h3>
                {isAdmin && (
                  <button
                    className="btn btn-sm"
                    onClick={() =>
                      setRepoTarget({ repo: null, nonce: Date.now() })
                    }
                  >
                    + Add a repository
                  </button>
                )}
              </div>
              <p className="auto-blurb">
                Indexed under <span className="mono">code/&lt;name&gt;/</span>{" "}
                and readable by everyone on this brain and by the agent.
              </p>
              <div className="auto-rows">
                {repoList.map((repo) => (
                  <RepoRow
                    key={repo.name}
                    repo={repo}
                    isAdmin={isAdmin}
                    syncing={syncing === repo.name}
                    result={syncResults[repo.name] ?? null}
                    onToggle={(r, enabled) => void saveRepo(r, { enabled })}
                    onSync={(r) => void syncRepo(r)}
                    onEdit={(r) =>
                      setRepoTarget({ repo: r, nonce: Date.now() })
                    }
                    onDelete={(r) => void removeRepo(r)}
                  />
                ))}
              </div>
            </section>
          )}

          {isAdmin && !empty && (
            <section className="card auto-panel">
              <div className="auto-panel-head">
                <h3>Scheduled reviews</h3>
                <button
                  className="btn btn-sm"
                  disabled={repoNames.length === 0}
                  title={
                    repoNames.length === 0
                      ? "Add a repository first"
                      : undefined
                  }
                  onClick={() =>
                    setReviewTarget({ job: null, nonce: Date.now() })
                  }
                >
                  + Schedule a review
                </button>
              </div>
              <p className="auto-blurb">
                On an interval: sync, read what changed since the last look,
                pull context from the brain, write{" "}
                <span className="mono">
                  reviews/&lt;repo&gt;-&lt;date&gt;.md
                </span>
                . Nothing new means nothing written.
              </p>
              {reviewJobs.length === 0 ? (
                <p className="muted auto-none">
                  No reviews scheduled yet. Schedule one, then press Review now
                  to see what it does before its first interval.
                </p>
              ) : (
                <div className="auto-rows">
                  {reviewJobs.map((job) => (
                    <ReviewJobRow
                      key={job.name}
                      job={job}
                      result={runResults[job.name] ?? null}
                      running={running === job.name}
                      onToggle={(j, enabled) => void saveJob(j, { enabled })}
                      onRun={(j) => void runJob(j)}
                      onEdit={(j) =>
                        setReviewTarget({ job: j, nonce: Date.now() })
                      }
                      onDelete={(j) => void removeJob(j)}
                    />
                  ))}
                </div>
              )}
            </section>
          )}

          {(reviews.length > 0 || !empty) && (
            <section className="card auto-panel">
              <div className="auto-panel-head">
                <h3>Reviews</h3>
              </div>
              <p className="auto-blurb">
                Newest first. Open one and tick a finding to approve it; an
                agent may then act on it through{" "}
                <span className="mono">approved_findings</span>.
              </p>
              {reviews.length === 0 ? (
                <p className="muted auto-none">No reviews yet.</p>
              ) : (
                <div className="auto-rows">
                  {reviews.map((r) => (
                    <div className="auto-row review-row" key={r.path}>
                      <div className="auto-row-head">
                        <p className="auto-sentence">
                          <button
                            className="link-btn"
                            onClick={() => onVaultPath(r.path)}
                          >
                            {r.title || r.name}
                          </button>
                        </p>
                        <div className="auto-row-actions">
                          <Severities r={r} />
                          <button
                            className="btn btn-sm"
                            onClick={() => onVaultPath(r.path)}
                          >
                            Open
                          </button>
                        </div>
                      </div>
                      <p className="auto-row-meta">
                        <span className="mono">{r.repo || "?"}</span>
                        {r.reviewed && (
                          <span className="muted mono"> · {r.reviewed}</span>
                        )}
                        {r.date && (
                          <span className="muted"> · {runWhen(r.date)}</span>
                        )}
                        {r.job && (
                          <span className="muted"> · by “{r.job}”</span>
                        )}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {!empty && (
            <p className="muted extend-foot">
              In Chat, ask for a review of any repository here and the agent
              uses the same tools. The <span className="mono">code-review</span>{" "}
              skill under Settings › Extend spells out the procedure.
            </p>
          )}
        </div>
      </div>

      {repoTarget && repos && (
        <RepoForm
          key={repoTarget.nonce}
          target={repoTarget}
          meta={repos}
          onClose={() => setRepoTarget(null)}
          onSaved={(saved, isNew) => {
            setRepoTarget(null);
            loadRepos();
            loadJobs();
            if (isNew) void syncRepo(saved);
          }}
        />
      )}
      {reviewTarget && (
        <ReviewForm
          key={reviewTarget.nonce}
          target={reviewTarget}
          repos={repoNames}
          modes={jobs.review_modes}
          defaultFocus={jobs.default_focus}
          onClose={() => setReviewTarget(null)}
          onSaved={() => {
            setReviewTarget(null);
            loadJobs();
          }}
        />
      )}
    </div>
  );
}
