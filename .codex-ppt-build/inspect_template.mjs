import fs from "node:fs/promises";
import { pathToFileURL } from "node:url";

const artifactTool = await import(pathToFileURL(
  "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs",
).href);
const { FileBlob, PresentationFile } = artifactTool;

const input = "/Users/geniusalu/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_f2dv19yyfpih22_853a/temp/drag/严颖PPT模板 - 副本.pptx";
const output = "/Users/geniusalu/Documents/brain-drone-swarm-console/.codex-ppt-build/template-inspection";
await fs.mkdir(output, { recursive: true });

const presentation = await PresentationFile.importPptx(await FileBlob.load(input));
const content = await presentation.inspect({
  select: "$.slides[*]",
  include: [
    "id", "index", "layout", "title",
    "elements.id", "elements.kind", "elements.name", "elements.text",
    "elements.position", "elements.style", "notes.text",
  ],
});
await fs.writeFile(`${output}/content.ndjson`, content.ndjson);
const layouts = await presentation.inspect({
  select: "$.layouts[*]",
  include: ["id", "name", "placeholders", "slideIndexes"],
});
await fs.writeFile(`${output}/layouts.ndjson`, layouts.ndjson);
await fs.writeFile(
  `${output}/summary.json`,
  JSON.stringify({
    slideSize: presentation.slideSize,
    slideCount: presentation.slides.items.length,
    masters: presentation.masters.items.map((master) => ({ id: master.id, name: master.name })),
    layouts: presentation.layouts.items.map((layout) => ({
      id: layout.id,
      name: layout.name,
      placeholders: layout.placeholders.summary(),
    })),
  }, null, 2),
);

for (const [index, slide] of presentation.slides.items.entries()) {
  const png = await presentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(`${output}/slide-${String(index + 1).padStart(2, "0")}.png`, new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: "layout", detail: "full" });
  await fs.writeFile(`${output}/slide-${String(index + 1).padStart(2, "0")}.layout.json`, await layout.text());
}

const montage = await presentation.export({ format: "webp", montage: true, scale: 1 });
await fs.writeFile(`${output}/montage.webp`, new Uint8Array(await montage.arrayBuffer()));
console.log(JSON.stringify({ output, slides: presentation.slides.items.length, slideSize: presentation.slideSize }, null, 2));
