import { useState } from "react";
import { apiSend } from "../api";
import type { LibrarySkill } from "../types";

/**
 * Ready-made skills, one click each.
 *
 * Most people do not want to write a skill; they want the one that already
 * does the thing. So the library is always on the page — an empty Skills
 * section shows this, never an empty list — and writing your own stays
 * available one click further in.
 */
export default function SkillLibrary({
  skills,
  onAdded,
}: {
  skills: LibrarySkill[];
  onAdded: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (skills.length === 0) return null;

  const add = async (skill: LibrarySkill) => {
    setBusy(skill.name);
    setError(null);
    try {
      await apiSend("POST", `/api/extensions/library/skill/${encodeURIComponent(skill.name)}`);
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : `could not add ${skill.name}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="lib">
      <p className="label">Ready-made skills</p>
      <p className="ext-blurb">
        Add one, then edit it — each is a starting point meant to be made your own.
      </p>
      {error && <p className="ext-error">✗ {error}</p>}
      <div className="lib-grid">
        {skills.map((skill) => (
          <div className={skill.installed ? "lib-card lib-have" : "lib-card"} key={skill.name}>
            <p className="mono lib-name">{skill.name}</p>
            <p className="lib-desc">{skill.description}</p>
            {skill.installed ? (
              <span className="lib-installed">✓ Installed</span>
            ) : (
              <button
                className="btn btn-sm"
                onClick={() => void add(skill)}
                disabled={busy !== null}
              >
                {busy === skill.name ? "Adding…" : "Add"}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
