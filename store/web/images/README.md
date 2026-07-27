# Product photos go here

Drop your product images in this folder, named exactly as they appear in the
`image_filename` column of `data/products.csv`.

- **Format:** JPG or PNG
- **Shape:** roughly square works best in the catalog grid
- **Size:** aim for under 500KB each so pages load quickly

A product whose photo is missing shows a lettered placeholder rather than a
broken image, and `python -m app.seed` prints a warning listing exactly which
files it couldn't find.

In Phase 2 these move to Cloudflare R2 and this folder goes away.
