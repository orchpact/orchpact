# Third-party notices

Orchpact's original code is distributed under MIT. Dependencies and vendored files keep their own licenses.

Swagger UI Dist 5.34.0 is vendored in `src/orchpact/static/vendor/` for local API documentation. The bundle and stylesheet are unchanged from the [official npm archive](https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.34.0.tgz). Its Apache-2.0 [LICENSE](src/orchpact/static/vendor/LICENSE) and upstream [NOTICE](src/orchpact/static/vendor/NOTICE) are included. Archive SHA-256: `15cdd55151558cf29ee70fb485332251a9911ebe27414b243ddd10fef9942394`.

Python dependencies are declared in `pyproject.toml` and resolved in `uv.lock`. Binary wheels, the Python/PostgreSQL base images and OS packages carry additional upstream licenses and notices; preserve those when redistributing an image. Dependency metadata is not a complete image SBOM.

CrewAI is used as an installed dependency under its MIT license, with the resolved version recorded in uv.lock. No CrewAI source is copied into this repository. Its transitive packages retain their respective upstream licenses; the Orchpact MIT license does not replace them.

## Browser UI

The React components in `frontend/src/components/ui/` are adapted from the [shadcn/ui New York registry](https://ui.shadcn.com/r/styles/new-york-v4/button.json). The [MIT license](frontend/LICENSE-shadcn.txt) accompanies the component sources. npm integrity hashes and exact versions are in `frontend/package-lock.json`. The offline browser distribution includes [dependency notices](src/orchpact/static/ui/LICENSES.txt). The [wordmark license](src/orchpact/static/brand/LICENSE-wordmark.txt) accompanies the identity kit.
