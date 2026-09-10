import Extend from "./Extend";
import Automation from "./Automation";
import Admin from "./Admin";

export type SettingsSection = "extend" | "automation" | "admin";

/**
 * The three admin views under one tab. Everyday tabs are things people do;
 * these are things someone sets up once and returns to rarely, and ten tabs
 * across the top made the ones that matter daily harder to find.
 */
const SECTIONS: { id: SettingsSection; label: string; blurb: string }[] = [
  { id: "extend", label: "Extend", blurb: "Skills, connectors, plugins, MCP servers, templates" },
  { id: "automation", label: "Automation", blurb: "Tidying rules and scheduled jobs" },
  { id: "admin", label: "Admin", blurb: "Accounts, identity, index and model health" },
];

export default function Settings({
  active,
  section,
  onSection,
  self,
}: {
  active: boolean;
  section: SettingsSection;
  onSection: (section: SettingsSection) => void;
  self: string;
}) {
  return (
    <div className="split">
      <aside className="side settings-side">
        <div className="side-head">
          <span className="label">Settings</span>
        </div>
        <nav className="side-list" aria-label="Settings sections">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className={section === s.id ? "side-item active" : "side-item"}
              aria-current={section === s.id ? "page" : undefined}
              onClick={() => onSection(s.id)}
            >
              {s.label}
              <span className="side-item-sub">{s.blurb}</span>
            </button>
          ))}
        </nav>
      </aside>
      <div className="pane settings-pane">
        <div className={section === "extend" ? "view" : "view hidden"}>
          <Extend active={active && section === "extend"} />
        </div>
        <div className={section === "automation" ? "view" : "view hidden"}>
          <Automation active={active && section === "automation"} />
        </div>
        <div className={section === "admin" ? "view" : "view hidden"}>
          <Admin self={self} active={active && section === "admin"} />
        </div>
      </div>
    </div>
  );
}
