import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend } from "../api";
import { wsSubscribe } from "../ws";
import { CODE_JOB_KINDS, everyLabel, isoAgo, jobSentence, runWhen } from "../lib/automation";
import type { Job, JobList, JobRun, Repo, RepoList, RepoSync, Review } from "../types";
import RepoForm, { type RepoTarget } from "../components/RepoForm";
import ReviewForm, { type ReviewTarget } from "../components/ReviewForm";

const NO_REPOS: RepoList = { repos: [], providers: [], token_envs: {}, env_path: "" };
const NO_JOBS: JobList = {
  jobs: [],
  suggested: [],
  kinds: [],
  connectors: [],
  repos: [],
  review_modes: [],
  default_focus: "",
};

function refreshLabel(hours: number): string {
  if (hours <= 0) return "refreshed only when asked";
  return `refreshed ${everyLabel(hours)}`;
}

/** How the last sync went, in the two colours that mean it. */
function SyncState({ repo }: { repo: Repo }) {
  if (!repo.last_sync) {
    return <span className="muted">never synced</span>;
  }
  const ok = repo.last_status === "ok";
  return (
    <>
      <span className="muted">{isoAgo(repo.last_sync)}</span>
      <span className={ok ? "run-ok" : "run-fail"}>
        {ok ? "✓" : "✗"} {repo.last_detail || repo.last_status}
      </span>
      {ok && repo.head_subject && (
        <span className="muted repo-subject"> — “{repo.head_subject}”</span>
      )}
    </>
  );
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
  return (
    <div className="auto-row">
      <div className="auto-row-head">
        <p className={repo.enabled ? "auto-sentence" : "auto-sentence auto-off"}>
          <span className="mono">{repo.name}</span>
          <span className="badge repo-badge">{repo.provider}</span>
          <a className="repo-link" href={repo.url} target="_blank" rel="noreferrer">
            {repo.slug}
          </a>
          {repo.branch && <span className="muted"> · {repo.branch}</span>}
        </p>
        {isAdmin && (
          <div className="auto-row-actions">
            <label className="toggle">
              <input
                type="checkbox"
                checked={repo.enabled}
                onChange={(e) => onToggle(repo, e.target.checked)}
              />
              <span>Enabled</span>
            </label>
            <button
              className="btn btn-sm"
              onClick={() => onSync(repo)}
              disabled={syncing || !repo.enabled}
            >
              {syncing ? "Syncing…" : "Sync now"}
            </button>
            <button className="btn btn-sm" onClick={() => onEdit(repo)}>
              Edit
            </button>
            <button className="btn btn-sm danger" onClick={() => onDelete(repo)}>
              Delete
            </button>
          </div>
        )}
      </div>
      <p className="auto-row-meta">
        <span className="mono">{repo.prefix}/</span>
        <span className="muted"> · {refreshLabel(repo.sync_hours)} · </span>
        <SyncState repo={repo} />
      </p>
      {!repo.token_present && (
        <p className="auto-row-meta muted">
          No token in <span className="mono">{repo.token_env}</span> — fine for a public
          repository; a private one will not sync until it is set.
        </p>
      )}
      {result && (
        <p className={result.status === "ok" ? "run-ok auto-ran" : "run-fail auto-ran"}>
          {result.status === "ok" ? "✓" : "✗"} synced just now — {result.detail || result.status}
        </p>
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
  const ok = job.last_status === "ok";
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
            {running ? "Reviewing…" : "Review now"}
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
        <span className="muted"> · looking for {String(job.settings.focus ?? "").slice(0, 80)}</span>
        {job.settings.channel ? <span className="muted"> · tells #{String(job.settings.channel)}</span> : null}
      </p>
      <p className="auto-row-meta">
        {job.last_run ? (
          <>
            <span className="muted">{isoAgo(job.last_run)} </span>
            <span className={ok ? "run-ok" : "run-fail"}>
              {ok ? "✓" : "✗"} {job.last_detail || job.last_status}
            </span>
          </>
        ) : (
          <span className="muted">never run</span>
        )}
      </p>
      {result && (
        <p className={result.status === "ok" ? "run-ok auto-ran" : "run-fail auto-ran"}>
          {result.status === "ok" ? "✓" : "✗"} ran just now — {result.detail || result.status}
        </p>
      )}
    </div>
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
  const [repos, setRepos] = useState<RepoList>(NO_REPOS);
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
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load repositories"));
  }, []);
  const loadJobs = useCallback(() => {
    if (!isAdmin) return;
    apiGet<JobList>("/api/jobs")
      .then((r) => setJobs({ ...NO_JOBS, ...r }))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load reviews"));
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
        if (ev.type === "vault_changed" && ev.vault === "shared" && ev.path.startsWith("reviews/")) {
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
      setError(e instanceof Error ? e.message : "the sync could not be started");
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
      setError(e instanceof Error ? e.message : "could not save that repository");
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
    if (!window.confirm(`Delete the scheduled review "${job.name}"? Reviews it wrote stay in the vault.`)) {
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

  const reviewJobs = jobs.jobs.filter((j) => CODE_JOB_KINDS.has(j.kind));
  const repoNames = repos.repos.filter((r) => r.enabled).map((r) => r.name);

  return (
    <div className="automation-view code-view">
      <div className="wrap auto-wrap">
        <div className="auto-head">
          <h2>Code</h2>
          <p className="auto-lead">
            Repositories the brain can read, and what it writes about them. Add a repo and the
            agent searches your code and your notes together; schedule a review and it reads
            what changed, with the brain open, and leaves findings for you to approve.
          </p>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>✗ {error}</span>
            <button className="btn btn-sm" onClick={() => setError(null)}>
              Dismiss
            </button>
          </div>
        )}

        <section className="card auto-panel">
          <div className="auto-panel-head">
            <h3>Repositories</h3>
            {isAdmin && (
              <button
                className="btn btn-sm"
                onClick={() => setRepoTarget({ repo: null, nonce: Date.now() })}
              >
                + Add a repository
              </button>
            )}
          </div>
          <p className="auto-blurb">
            GitHub or GitLab, public or private. A private one needs a token in{" "}
            <span className="mono">{repos.env_path || ".env"}</span>; the brain keeps a clone,
            refreshes it on a schedule, and indexes it under{" "}
            <span className="mono">code/&lt;name&gt;/</span>.
          </p>
          {repos.repos.length === 0 ? (
            <p className="muted auto-none">
              {isAdmin
                ? "No repositories yet. Add one and the agent can read it on the next turn."
                : "No repositories yet — an admin adds them here."}
            </p>
          ) : (
            <div className="auto-rows">
              {repos.repos.map((repo) => (
                <RepoRow
                  key={repo.name}
                  repo={repo}
                  isAdmin={isAdmin}
                  syncing={syncing === repo.name}
                  result={syncResults[repo.name] ?? null}
                  onToggle={(r, enabled) => void saveRepo(r, { enabled })}
                  onSync={(r) => void syncRepo(r)}
                  onEdit={(r) => setRepoTarget({ repo: r, nonce: Date.now() })}
                  onDelete={(r) => void removeRepo(r)}
                />
              ))}
            </div>
          )}
        </section>

        {isAdmin && (
          <section className="card auto-panel">
            <div className="auto-panel-head">
              <h3>Scheduled reviews</h3>
              <button
                className="btn btn-sm"
                disabled={repoNames.length === 0}
                title={repoNames.length === 0 ? "Add a repository first" : undefined}
                onClick={() => setReviewTarget({ job: null, nonce: Date.now() })}
              >
                + Schedule a review
              </button>
            </div>
            <p className="auto-blurb">
              On an interval, the agent syncs the repo, reads what changed since it last looked,
              pulls context from the brain, and writes{" "}
              <span className="mono">reviews/&lt;repo&gt;-&lt;date&gt;.md</span> in the shared
              vault. Nothing new means nothing written.
            </p>
            {reviewJobs.length === 0 ? (
              <p className="muted auto-none">
                {repoNames.length === 0
                  ? "Add a repository, then schedule a review of it."
                  : "No reviews scheduled. Schedule one, or press Review now once it exists to see what it does."}
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
                    onEdit={(j) => setReviewTarget({ job: j, nonce: Date.now() })}
                    onDelete={(j) => void removeJob(j)}
                  />
                ))}
              </div>
            )}
          </section>
        )}

        <section className="card auto-panel">
          <div className="auto-panel-head">
            <h3>Reviews</h3>
          </div>
          <p className="auto-blurb">
            What has been written, newest first. Open one and tick a finding to approve it for
            automated work — an agent may then pick it up through{" "}
            <span className="mono">approved_findings</span>; nothing acts on an unticked one.
          </p>
          {reviews.length === 0 ? (
            <p className="muted auto-none">No reviews yet.</p>
          ) : (
            <div className="auto-rows">
              {reviews.map((r) => (
                <div className="auto-row review-row" key={r.path}>
                  <div className="auto-row-head">
                    <p className="auto-sentence">
                      <button className="link-btn" onClick={() => onVaultPath(r.path)}>
                        {r.title || r.name}
                      </button>
                    </p>
                    <div className="auto-row-actions">
                      <span className={r.approved > 0 ? "run-ok" : "muted"}>
                        {r.findings === 0
                          ? "no findings"
                          : `${r.findings} finding${r.findings === 1 ? "" : "s"}, ${r.approved} approved`}
                      </span>
                      <button className="btn btn-sm" onClick={() => onVaultPath(r.path)}>
                        Open
                      </button>
                    </div>
                  </div>
                  <p className="auto-row-meta">
                    <span className="mono">{r.repo || "?"}</span>
                    {r.reviewed && <span className="muted mono"> · {r.reviewed}</span>}
                    {r.date && <span className="muted"> · {runWhen(r.date)}</span>}
                    {r.job && <span className="muted"> · by “{r.job}”</span>}
                  </p>
                </div>
              ))}
            </div>
          )}
        </section>

        <p className="muted extend-foot">
          In Chat, ask for a review of any repo here and the agent uses the same tools —
          <span className="mono"> list_repos</span>, <span className="mono">repo_diff</span>,{" "}
          <span className="mono">read_file</span>. The <span className="mono">code-review</span>{" "}
          and <span className="mono">deep-research</span> skills in Extend spell out the
          procedure.
        </p>
      </div>

      {repoTarget && (
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
