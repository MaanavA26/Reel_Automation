import type { ReactElement } from "react";

import { HomePage } from "../pages/HomePage";

export function AppShell(): ReactElement {
  return (
    <div className="app-shell">
      <main className="app-shell__container">
        <p className="app-shell__eyebrow">Reel Automation</p>
        <h1 className="app-shell__title">Production scaffold</h1>
        <div className="app-shell__body">
          <HomePage />
        </div>
      </main>
    </div>
  );
}
