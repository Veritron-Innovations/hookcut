import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lyric_align import _chunk_words_into_lines, _time_warp_words_gap_aware

def test_chunk_breaks_on_real_silence():
    words=[{'word':'one','start':1.0,'end':1.4},{'word':'two','start':1.5,'end':1.9},{'word':'three','start':3.0,'end':3.5}]
    lines=_chunk_words_into_lines(words)
    assert len(lines)==2
    assert lines[0]['end']==1.9
    assert lines[1]['start']==3.0

def test_gap_aware_mapping_preserves_large_pause():
    whisper=[{'word':'a','start':1.0,'end':1.3},{'word':'b','start':1.4,'end':1.8},{'word':'c','start':4.0,'end':4.3},{'word':'d','start':4.4,'end':4.8}]
    aligned=_time_warp_words_gap_aware(['A','B','C','D'], whisper, 6.0)
    assert len(aligned)==4
    assert aligned[1]['end']<2.0
    assert aligned[2]['start']>=4.0
