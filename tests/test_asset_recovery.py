import sys
import struct
import zlib
from types import SimpleNamespace
from pathlib import Path
from PIL import Image
import pytest
from app import raghu_pipeline as raghu, raghu_narration as voice, pipeline, store, media


def test_explicit_raghu_scene_regeneration_removes_actual_files_only(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path);monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('S3_BUCKET',raising=False);store.init()
    ident='a'*32;root=store.folder(ident);root.mkdir()
    store.save(root/'raghu-plan-v2.json',{'shots':[dict(id='001'),dict(id='002')]})
    for name in ['still-001.png','shot-001.mp4','motion-001.review.json','still-001.png.review.json','final.mp4',
                 'shot-002.mp4','character-A.png','narration-clone.wav']:
        (root/name).write_bytes(b'original')
    pipeline.archive_scene(ident,1)
    assert not (root/'still-001.png').exists() and not (root/'shot-001.mp4').exists()
    assert (root/'shot-002.mp4').exists() and (root/'character-A.png').exists()
    assert (root/'motion-001.invalidate.json').is_file()
    assert len(list((root/'history').rglob('shot-001.mp4')))==1


def test_corrupt_saved_png_is_quarantined_and_only_dependents_invalidated(tmp_path):
    (tmp_path/'character-A.png').write_bytes(b'broken image')
    for name in ['still-001.png','still-002.png','still-003.png']:
        Image.new('RGB',(16,16),'blue').save(tmp_path/name)
    for ident in ('001','002','003'):(tmp_path/f'shot-{ident}.mp4').write_bytes(b'video')
    tasks=[dict(file='character-A.png',refs=[]),dict(file='still-001.png',refs=['results/character-A.png']),
           dict(file='still-002.png',refs=[]),dict(file='still-003.png',refs=['results/still-001.png'])]
    raghu.recover_saved_images(tmp_path,tasks,lambda:None)
    assert not (tmp_path/'character-A.png').exists()
    assert not (tmp_path/'still-001.png').exists() and not (tmp_path/'shot-001.mp4').exists()
    assert not (tmp_path/'still-003.png').exists() and not (tmp_path/'shot-003.mp4').exists()
    assert (tmp_path/'still-002.png').exists() and (tmp_path/'shot-002.mp4').exists()
    assert list((tmp_path/'history').rglob('character-A.png'))


def test_corrupt_dialogue_audio_is_recreated_but_good_audio_is_kept(tmp_path,monkeypatch):
    calls=[]
    class Communicate:
        def __init__(self,text,*a,**k):self.text=text
        async def save(self,path):calls.append(self.text);Path(path).write_bytes(b'mp3')
    monkeypatch.setitem(sys.modules,'edge_tts',SimpleNamespace(Communicate=Communicate))
    (tmp_path/'narration-001.wav').write_bytes(b'broken')
    media.tone(tmp_path/'narration-002.wav',1,'none')
    good=(tmp_path/'narration-002.wav').read_bytes()
    monkeypatch.setattr(media,'ff',lambda *a,**k:media.tone(Path(a[-1]),1,'none'))
    voice.produce(tmp_path,[dict(id='001',narration='पहली पंक्ति।',dialogue_voice='female'),
                            dict(id='002',narration='दूसरी पंक्ति।',dialogue_voice='female')],lambda:None,lambda *a:None)
    assert calls==['पहली पंक्ति।'] and voice.readable_voice(tmp_path/'narration-001.wav')
    assert (tmp_path/'narration-002.wav').read_bytes()==good


def test_png_with_valid_structure_but_bad_pixels_is_not_accepted(tmp_path):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    path=tmp_path/'bad.png'
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',32,32,8,2,0,0,0))+
                     chunk(b'IDAT',zlib.compress(b'x'))+chunk(b'IEND',b''))
    with Image.open(path) as image:image.verify()
    with pytest.raises(OSError):raghu.check_image_file(path)


def test_truncated_wav_with_extra_metadata_is_not_accepted(tmp_path):
    path=tmp_path/'voice.wav'
    media.ff('-f','lavfi','-i','sine=frequency=440:sample_rate=24000','-t','1',path)
    path.write_bytes(path.read_bytes()[:-16])
    assert not voice.readable_voice(path)
