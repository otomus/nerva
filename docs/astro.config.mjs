import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  site: "https://otomus.github.io",
  base: "/nerva/docs",
  integrations: [
    starlight({
      title: "nerva",
      description: "Composable Agent Primitives",
      customCss: ["./src/styles/custom.css"],
      head: [
        {
          tag: "link",
          attrs: {
            rel: "preconnect",
            href: "https://fonts.googleapis.com",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "preconnect",
            href: "https://fonts.gstatic.com",
            crossorigin: true,
          },
        },
      ],
      social: {
        github: "https://github.com/otomus/nerva",
      },
      components: {
        SiteTitle: "./src/components/SiteTitle.astro",
        ThemeSelect: "./src/components/ThemeSelect.astro",
      },
      sidebar: [
        {
          label: "Architecture",
          items: [
            { label: "Overview", slug: "architecture" },
          ],
        },
        {
          label: "Getting Started",
          items: [
            { label: "Python", slug: "getting-started/python" },
            { label: "TypeScript", slug: "getting-started/typescript" },
          ],
        },
        {
          label: "Primitives",
          items: [
            { label: "Overview", slug: "primitives/overview" },
            { label: "ExecContext", slug: "primitives/context" },
            { label: "Router", slug: "primitives/router" },
            { label: "Runtime", slug: "primitives/runtime" },
            { label: "Tools", slug: "primitives/tools" },
            { label: "Memory", slug: "primitives/memory" },
            { label: "Responder", slug: "primitives/responder" },
            { label: "Registry", slug: "primitives/registry" },
            { label: "Policy", slug: "primitives/policy" },
          ],
        },
        {
          label: "CLI",
          items: [
            { label: "Overview", slug: "cli/overview" },
            { label: "nerva new", slug: "cli/new" },
            { label: "nerva generate", slug: "cli/generate" },
            { label: "nerva dev & test", slug: "cli/dev" },
            { label: "Plugins", slug: "cli/plugins" },
          ],
        },
        {
          label: "Ecosystem",
          items: [
            { label: "NATS JetStream", slug: "ecosystem/nats" },
            { label: "mcp-armor", slug: "ecosystem/mcp-armor" },
            { label: "OpenTelemetry", slug: "ecosystem/opentelemetry" },
          ],
        },
        {
          label: "Guides",
          items: [
            { label: "Testing", slug: "guides/testing" },
            { label: "Migration Guide", slug: "guides/migration" },
            { label: "Cookbook", slug: "guides/cookbook" },
          ],
        },
      ],
    }),
  ],
});
