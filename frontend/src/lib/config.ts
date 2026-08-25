/** Browser-visible configuration, sourced from Vite env variables. */
export interface AppConfig {
  readonly apiBaseUrl: string
}

export const appConfig: AppConfig = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000',
}
