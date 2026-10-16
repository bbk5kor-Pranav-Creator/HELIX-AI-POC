# React + Vite

## Saved Paste Text sources

In the Admin Dashboard, **Paste Text** reopens the most recently saved source.
**Source Management > Edit** loads a specific source from the backend by ID,
including its full text and transformation notes. **Update Text** keeps that ID
and replaces its searchable chunks. **Add New Source** opens a blank form;
closing or cancelling without saving does not write changes.

Saved entries have a confirmation-protected **Delete** action. Deletion removes
the source and its chunks permanently; subsequent Employee interview requests
cannot retrieve the deleted text.

The existing bearer session protects all source operations. In addition to text
creation and source listing, the backend exposes Admin-only `GET`, `PUT`, and
`DELETE /api/knowledge/sources/{source_id}` endpoints. The notes column is added
automatically to older SQLite databases; previously unsaved notes cannot be
recovered.

Run backend regression tests from the repository root with:

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m unittest -v test_knowledge
Pop-Location
```

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.
