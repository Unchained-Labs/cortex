import RulesPanel from "../components/RulesPanel";
import JobsPanel from "../components/JobsPanel";

/**
 * Rules and scheduled jobs on one page, because they are one idea: things
 * the brain does without being asked. Rules say *what* should happen to a
 * note; jobs say *when* anything happens at all.
 */
export default function Automation({ active }: { active: boolean }) {
  return (
    <div className="automation-view">
      <div className="wrap auto-wrap">
        <div className="auto-head">
          <h2>Automation</h2>
          <p className="auto-lead">
            What the brain does without being asked. Rules file notes where they belong;
            jobs decide how often anything runs.
          </p>
        </div>

        <RulesPanel active={active} />
        <JobsPanel active={active} />
      </div>
    </div>
  );
}
