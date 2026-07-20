import { joinSession } from "@github/copilot-sdk/extension";

const API_BASE = "https://vmsku-api-func-cus.azurewebsites.net/api";
const TIMEOUT_MS = 60_000;

async function apiFetch(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
        const res = await fetch(`${API_BASE}${path}`, {
            ...options,
            signal: controller.signal,
        });
        clearTimeout(timer);
        if (!res.ok) {
            const body = await res.text().catch(() => "");
            return { error: `HTTP ${res.status}: ${body || res.statusText}` };
        }
        return await res.json();
    } catch (err) {
        clearTimeout(timer);
        return { error: err.name === "AbortError" ? "Request timed out (60s)" : err.message };
    }
}

const session = await joinSession({
    hooks: {
        onSessionStart: async () => {
            await session.log("Azure VM SKU Alternatives tools loaded (find_alternative_skus, compare_sku_details, list_vm_skus, health_check)");
        },
    },
    tools: [
        {
            name: "health_check",
            description: "Verify connectivity to the Azure VM SKU Alternatives API.",
            parameters: { type: "object", properties: {} },
            handler: async () => {
                const data = await apiFetch("/health");
                if (data.error) return `API unreachable: ${data.error}`;
                return `API is healthy. Environment: ${data.environment ?? "unknown"}. Timestamp: ${data.timestamp ?? "unknown"}`;
            },
        },
        {
            name: "find_alternative_skus",
            description:
                "Find Azure VM SKUs similar to a target SKU in a region, ranked by similarity score. " +
                "Use this to discover drop-in replacements, cheaper alternatives, or SKUs with a different CPU vendor/architecture.",
            parameters: {
                type: "object",
                properties: {
                    sku_name: {
                        type: "string",
                        description: "The target Azure VM SKU name, e.g. Standard_D8s_v5",
                    },
                    location: {
                        type: "string",
                        description: "Azure region slug, e.g. eastus, westus2, westeurope, eastasia",
                    },
                    top_n: {
                        type: "integer",
                        description: "Maximum number of alternatives to return (default 10, max 50)",
                        default: 10,
                    },
                    cpu_vendor: {
                        type: "string",
                        description: "Filter by CPU vendor: Intel, AMD, or ARM",
                        enum: ["Intel", "AMD", "ARM"],
                    },
                    max_price_per_hour: {
                        type: "number",
                        description: "Only return SKUs with Linux hourly price at or below this value (USD)",
                    },
                },
                required: ["sku_name", "location"],
            },
            handler: async (args) => {
                const body = {
                    skuName: args.sku_name,
                    location: args.location,
                    topN: 200,
                };
                const data = await apiFetch("/compare_vms", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                if (data.error) return `Error: ${data.error}`;

                const target = data.targetSku ?? {};
                let alts = data.alternatives ?? [];

                // Apply client-side filters (API doesn't filter these server-side)
                if (args.cpu_vendor) {
                    alts = alts.filter(a => a.cpuVendor === args.cpu_vendor);
                }
                if (args.max_price_per_hour != null) {
                    alts = alts.filter(a => a.pricing?.hourlyPrice != null && a.pricing.hourlyPrice <= args.max_price_per_hour);
                }
                alts = alts.slice(0, args.top_n ?? 10);

                if (alts.length === 0) return "No alternatives found for the given criteria.";

                const lines = [
                    `**Target: ${target.name}** — ${target.vCPUs} vCPUs, ${target.memoryGB} GB RAM, ${target.cpuVendor} ${target.architecture}`,
                    `Linux: $${target.pricing?.hourlyPrice?.toFixed(4)}/hr | Windows: $${target.pricing?.hourlyPriceWindows?.toFixed(4) ?? "N/A"}/hr`,
                    "",
                    `**Top ${alts.length} alternatives in ${args.location}:**`,
                    "",
                    "| Rank | SKU | Score | Vendor | Arch | vCPUs | RAM (GB) | Linux $/hr | Win $/hr |",
                    "|------|-----|-------|--------|------|-------|----------|-----------|---------|",
                ];

                for (const [i, alt] of alts.entries()) {
                    const caps = alt.capabilities ?? {};
                    lines.push(
                        `| ${i + 1} | ${alt.name} | ${alt.similarityScore?.toFixed(1)}% | ${alt.cpuVendor ?? "?"} | ${alt.architecture ?? "?"} | ${caps.vCPUs ?? "?"} | ${caps.memoryGB ?? "?"} | $${alt.pricing?.hourlyPrice?.toFixed(4) ?? "N/A"} | $${alt.pricing?.hourlyPriceWindows?.toFixed(4) ?? "N/A"} |`
                    );
                }

                return lines.join("\n");
            },
        },
        {
            name: "compare_sku_details",
            description:
                "Side-by-side detailed comparison of two Azure VM SKUs in a region, including capabilities, pricing, and differences.",
            parameters: {
                type: "object",
                properties: {
                    sku1: { type: "string", description: "First SKU name, e.g. Standard_D8s_v5" },
                    sku2: { type: "string", description: "Second SKU name, e.g. Standard_D8as_v5" },
                    location: { type: "string", description: "Azure region slug, e.g. eastus" },
                },
                required: ["sku1", "sku2", "location"],
            },
            handler: async (args) => {
                const data = await apiFetch(
                    `/compare_details?target=${encodeURIComponent(args.sku1)}&alternative=${encodeURIComponent(args.sku2)}&location=${encodeURIComponent(args.location)}`
                );
                if (data.error) return `Error: ${data.error}`;
                return JSON.stringify(data, null, 2);
            },
        },
        {
            name: "list_vm_skus",
            description:
                "List Azure VM SKUs available in a region. Optionally filter by minimum/maximum vCPUs, memory, or CPU vendor.",
            parameters: {
                type: "object",
                properties: {
                    location: { type: "string", description: "Azure region slug, e.g. eastus" },
                    cpu_vendor: {
                        type: "string",
                        description: "Filter by CPU vendor: Intel, AMD, or ARM",
                        enum: ["Intel", "AMD", "ARM"],
                    },
                    min_vcpus: { type: "integer", description: "Minimum vCPU count" },
                    max_vcpus: { type: "integer", description: "Maximum vCPU count" },
                },
                required: ["location"],
            },
            handler: async (args) => {
                const params = new URLSearchParams({ location: args.location });

                const data = await apiFetch(`/skus?${params}`);
                if (data.error) return `Error: ${data.error}`;

                let skus = Array.isArray(data) ? data : data.skus ?? [];
                if (skus.length === 0) return "No SKUs found for the given criteria.";

                // Apply client-side filters (vCPUs and memoryGB are top-level; cpuVendor too)
                if (args.cpu_vendor) skus = skus.filter(s => s.cpuVendor === args.cpu_vendor);
                if (args.min_vcpus != null) skus = skus.filter(s => s.vCPUs >= args.min_vcpus);
                if (args.max_vcpus != null) skus = skus.filter(s => s.vCPUs <= args.max_vcpus);

                if (skus.length === 0) return "No SKUs found for the given criteria.";

                const lines = [
                    `**${skus.length} SKUs available in ${args.location}**${args.cpu_vendor ? ` (${args.cpu_vendor})` : ""}`,
                    "",
                    "| SKU | vCPUs | RAM (GB) | Vendor | Arch |",
                    "|-----|-------|----------|--------|------|",
                ];

                for (const sku of skus.slice(0, 100)) {
                    lines.push(
                        `| ${sku.name} | ${sku.vCPUs ?? "?"} | ${sku.memoryGB ?? "?"} | ${sku.cpuVendor ?? "?"} | ${sku.architecture ?? "?"} |`
                    );
                }

                if (skus.length > 100) lines.push(`\n_...and ${skus.length - 100} more. Refine your filters to narrow results._`);

                return lines.join("\n");
            },
        },
    ],
});
