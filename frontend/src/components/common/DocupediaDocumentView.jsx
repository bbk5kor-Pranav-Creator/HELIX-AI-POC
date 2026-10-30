function provenance(block) {
  const details = [];
  if (block.page_start != null) {
    details.push(block.page_end && block.page_end !== block.page_start
      ? `Pages ${block.page_start}-${block.page_end}` : `Page ${block.page_start}`);
  }
  if (block.paragraph_index != null) details.push(`Paragraph ${block.paragraph_index + 1}`);
  if (block.table_index != null) details.push(`Table ${block.table_index + 1}`);
  if (block.locator?.line_start != null) {
    details.push(`Lines ${block.locator.line_start}-${block.locator.line_end ?? block.locator.line_start}`);
  }
  if (block.locator?.line_index != null) details.push(`Line ${block.locator.line_index + 1}`);
  return details.join(' · ') || 'Page location not available';
}

export default function DocupediaDocumentView({ document }) {
  const rowsByTable = new Map();
  document.blocks.forEach((block) => {
    if (block.block_type === 'table_row' && block.parent_block_ordinal != null) {
      const rows = rowsByTable.get(block.parent_block_ordinal) || [];
      rows.push(block);
      rowsByTable.set(block.parent_block_ordinal, rows);
    }
  });
  const tableOrdinals = new Set(document.blocks
    .filter((block) => block.block_type === 'table').map((block) => block.ordinal));

  const renderRows = (rows) => (
    <div className="docupedia-table-scroll">
      <table>
        <tbody>
          {rows.map((row) => (
            <tr key={row.ordinal}>
              {(row.cells?.length ? row.cells : [{ text: row.text }]).map((cell, index) => {
                const Cell = cell.is_header ? 'th' : 'td';
                return <Cell key={cell.cell_index ?? index}>{cell.text}</Cell>;
              })}
              <td className="docupedia-provenance">{provenance(row)} · Row {(row.row_index ?? 0) + 1}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  return (
    <section className="docupedia-document" aria-label="Original structured extraction">
      {document.blocks.map((block) => {
        if (block.block_type === 'table_row' && tableOrdinals.has(block.parent_block_ordinal)) return null;
        const Heading = `h${Math.min(6, Math.max(1, block.heading_level || 1))}`;
        return (
          <div className="docupedia-block" key={block.ordinal}>
            <small className="docupedia-provenance">{provenance(block)}</small>
            {block.block_type === 'heading' ? (
              <Heading>{block.text}</Heading>
            ) : block.block_type === 'table' ? (
              <>
                {block.text && <p>{block.text}</p>}
                {renderRows(rowsByTable.get(block.ordinal) || [])}
              </>
            ) : block.block_type === 'table_row' ? renderRows([block]) : (
              <>
                {block.block_type !== 'paragraph' && <small>{block.block_type}</small>}
                <p>{block.text}</p>
                {block.cells?.length > 0 && renderRows([block])}
              </>
            )}
          </div>
        );
      })}
    </section>
  );
}
