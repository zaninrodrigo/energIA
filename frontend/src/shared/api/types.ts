/** Generic paginated response shape shared by list endpoints across the API. */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
