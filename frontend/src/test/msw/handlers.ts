import { http, HttpResponse } from "msw";
import { suministrosFixture } from "../fixtures";

export const handlers = [
  http.get("http://localhost:8000/api/v1/suministros", () => {
    return HttpResponse.json(suministrosFixture);
  }),
];
