import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error("Usage: node build_excel.mjs <input.json> <output.xlsx>");
}

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const rows = payload.rows;
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("智谱论文");
sheet.showGridLines = false;

const headers = [
  "论文主题",
  "作者",
  "汉译",
  "期刊名称",
  "期刊号",
  "发表时间",
  "论文附件（URL）",
  "ArvixURL",
];
const values = rows.map((row) => [
  row.title,
  row.authors,
  row.translated_title,
  row.journal_name,
  row.journal_issue,
  new Date(`${row.published}T00:00:00Z`),
  row.pdf_url,
  row.arxiv_url,
]);

sheet.getRangeByIndexes(0, 0, values.length + 1, headers.length).values = [
  headers,
  ...values,
];
sheet.freezePanes.freezeRows(1);
sheet.getRange("A1:H1").format = {
  fill: "#245B78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#D7E1E8" },
};
sheet.getRange(`A2:H${values.length + 1}`).format = {
  verticalAlignment: "top",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#E2E8EC" },
};
sheet.getRange(`F2:F${values.length + 1}`).format.numberFormat = "yyyy-mm-dd";
sheet.getRange("A:A").format.columnWidth = 42;
sheet.getRange("B:B").format.columnWidth = 48;
sheet.getRange("C:C").format.columnWidth = 36;
sheet.getRange("D:D").format.columnWidth = 22;
sheet.getRange("E:E").format.columnWidth = 16;
sheet.getRange("F:F").format.columnWidth = 13;
sheet.getRange("G:H").format.columnWidth = 38;
sheet.getRange("1:1").format.rowHeight = 32;
sheet.getRange(`2:${values.length + 1}`).format.rowHeight = 52;

const table = sheet.tables.add(`A1:H${values.length + 1}`, true, "ZhipuPapers");
table.style = "TableStyleMedium2";
table.showFilterButton = true;

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
