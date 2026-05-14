import { defineConfig } from "astro/config";

// GitHub Pages from the repo root will publish at https://<user>.github.io/<repo>.
// `site` and `base` get set later via env vars in the deploy workflow.
export default defineConfig({
  site: process.env.SITE_URL ?? "https://example.github.io",
  base: process.env.SITE_BASE ?? "/",
  trailingSlash: "ignore",
  build: {
    format: "directory",
  },
  markdown: {
    syntaxHighlight: "shiki",
    shikiConfig: {
      themes: { light: "github-light", dark: "github-dark" },
      wrap: true,
    },
  },
});
