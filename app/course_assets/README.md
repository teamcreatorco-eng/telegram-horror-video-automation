# Bundled approved course media

These files are included at the repository owner's explicit request so a clone or
Railway Docker build contains all approved default media:

- Four full music tracks and their 12-second narrated selection previews.
- The approved Web 6 narrator reference, `narrator.wav`.
- The approved combined intro/disclaimer, `opening.mp4`.

`manifest.json` records each binary's size and SHA-256 digest. These files are read
directly from the package on localhost and Railway, without a separate download,
upload, paid API request or GPU. Existing custom files in DATA_DIR and explicit
configuration paths still override defaults. Credentials are never included here.
