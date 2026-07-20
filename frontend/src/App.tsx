import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { RankingPage } from "./features/ranking/components/RankingPage";
import { SuministrosPage } from "./features/suministros/components/SuministrosPage";

const navLinkClassName = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive
      ? "bg-brand-subtle text-brand"
      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
  }`;

/** App shell: header (wordmark + primary nav) + router. `/` redirects to `/ranking` -- the
 *  Ranking de Riesgo dashboard is this project's demo centerpiece, so it's what people land
 *  on. */
function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <span className="text-lg font-semibold tracking-tight text-slate-900">EnergIA</span>
          <nav aria-label="Navegación principal" className="flex gap-2">
            <NavLink to="/suministros" className={navLinkClassName}>
              Suministros
            </NavLink>
            <NavLink to="/ranking" className={navLinkClassName}>
              Ranking de Riesgo
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/ranking" replace />} />
          <Route path="/suministros" element={<SuministrosPage />} />
          <Route path="/ranking" element={<RankingPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
