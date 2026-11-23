"""Install course music privately: python -m app.install_music SOURCE_DIRECTORY."""
import argparse
import shutil
import tempfile
from pathlib import Path

from . import media, raghu_music, store


def install(directory):
    root = store.DATA / '_music' / 'catalog'
    (root / 'previews').mkdir(parents=True, exist_ok=True)
    for ident, title in raghu_music.CATALOG.items():
        source = directory / f'{ident}.mp3'
        if not source.is_file():
            source = directory / f'{title}.mp3'
        if not source.is_file():
            raise ValueError(f'Missing course music file: {title}.mp3')
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            tmp = Path(tmp)
            track = tmp / f'{ident}.mp3'
            shutil.copyfile(source, track)
            if media.duration(track) < .25:
                raise ValueError(f'Unreadable music: {title}')
            if not any(s['codec_type'] == 'audio' for s in media.probe(track)['streams']):
                raise ValueError(f'Music file contains no audio: {title}')
            media.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(track),
                       '-map', '0:a:0', '-f', 'null', '-'])
            preview = tmp / f'{ident}.mp4'
            supplied_preview = directory / 'previews' / preview.name
            if supplied_preview.is_file():
                shutil.copyfile(supplied_preview, preview)
            else:
                bed = tmp / 'bed.wav'
                raghu_music.render(bed, 12, track)
                media.ff('-f', 'lavfi', '-i', 'color=c=0x18202c:s=640x360:r=24',
                         '-i', bed, '-vf', f"drawtext=text='{title}':fontcolor=white:fontsize=26:x=(w-text_w)/2:y=(h-text_h)/2",
                         '-t', '12', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                         '-c:a', 'aac', '-movflags', '+faststart', preview)
            media.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(preview), '-f', 'null', '-'])
            info = media.probe(preview)
            if not {'audio', 'video'} <= {s['codec_type'] for s in info['streams']}:
                raise ValueError(f'The preview for {title} must include audio and video')
            track.replace(root / track.name)
            preview.replace(root / 'previews' / preview.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    install(args.directory)
    print('Installed four private music tracks and previews.')


if __name__ == '__main__':
    main()
