import { useCallback, useEffect, useState } from "react";
import { isTypingTarget } from "./arcade";
import { ResourceExplorer } from "./ResourceExplorer";
import { ReviewQueue } from "./ReviewQueue";

const POINTS_PER_RESOLVE = 100;

const SCREENS = [
  { id: "queue", key: "1", title: "REVIEW QUEUE" },
  { id: "explorer", key: "2", title: "RESOURCE EXPLORER" },
] as const;

type ScreenId = (typeof SCREENS)[number]["id"];

export default function App() {
  const [screen, setScreen] = useState<ScreenId>("queue");
  const [score, setScore] = useState(0);
  const addPoints = useCallback(() => setScore((s) => s + POINTS_PER_RESOLVE), []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTypingTarget(event.target)) return;
      const chosen = SCREENS.find((s) => s.key === event.key);
      if (chosen) setScreen(chosen.id);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="mx-auto max-w-3xl p-6">
      <header className="mb-6">
        <div className="font-arcade mb-4 flex items-baseline justify-between">
          <h1 className="text-xl text-red-500">DATUM</h1>
          <p className="text-xs text-red-400">
            SCORE{" "}
            {/* Remounting on each change replays the pop animation. */}
            <span key={score} data-testid="score" className="score-pop inline-block text-slate-100">
              {String(score).padStart(6, "0")}
            </span>
          </p>
        </div>
        <nav aria-label="Screens" className="flex gap-2">
          {SCREENS.map((s) => (
            <button key={s.id} aria-current={s.id === screen ? "page" : undefined}
                    onClick={() => setScreen(s.id)}
                    className={`arcade-button ${s.id === screen ? "arcade-button-on" : ""}`}>
              {s.key} {s.title}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {screen === "queue" ? <ReviewQueue onResolved={addPoints} /> : <ResourceExplorer />}
      </main>
    </div>
  );
}
