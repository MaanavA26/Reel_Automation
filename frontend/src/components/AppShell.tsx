import { useState, type ReactElement } from "react";

import { HomePage } from "../pages/HomePage";
import { ResearchPage } from "../pages/ResearchPage";

type View = "home" | "research";

const NAV_ITEMS: ReadonlyArray<{ id: View; label: string }> = [
  { id: "home", label: "Overview" },
  { id: "research", label: "Deep Research" },
];

export function AppShell(): ReactElement {
  const [view, setView] = useState<View>("home");

  return (
    <div className="app-shell">
      <main className="app-shell__container">
        <p className="app-shell__eyebrow">Reel Automation</p>
        <h1 className="app-shell__title">Production scaffold</h1>
        <nav className="app-shell__nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={
                view === item.id
                  ? "app-shell__nav-item app-shell__nav-item--active"
                  : "app-shell__nav-item"
              }
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="app-shell__body">
          {view === "home" ? <HomePage /> : <ResearchPage />}
        </div>
      </main>
    </div>
  );
}
