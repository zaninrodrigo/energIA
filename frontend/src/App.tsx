import { Route, Routes } from "react-router-dom";
import { SuministrosPage } from "./features/suministros/components/SuministrosPage";

/** App shell: header + router. A single route today; the layout leaves room to grow into more
 *  screens (Dashboard Ejecutivo, historial de consumo, etc.) without restructuring. */
function App() {
  return (
    <div className="app-shell">
      <header>
        <h1>EnergIA — Suministros</h1>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SuministrosPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
