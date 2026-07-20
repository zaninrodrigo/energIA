import { http, HttpResponse } from "msw";
import { lotesFixture, rankingFixture, suministrosFixture } from "../fixtures";

export const handlers = [
  http.get("http://localhost:8000/api/v1/suministros", () => {
    return HttpResponse.json(suministrosFixture);
  }),
  http.get("http://localhost:8000/api/v1/lotes", () => {
    return HttpResponse.json(lotesFixture);
  }),
  http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", () => {
    return HttpResponse.json(rankingFixture);
  }),
];
