import tempfile
from pathlib import Path
import numpy as np
from bounded_validation import FalsePositiveWindows

with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'features.npy'
    raw=np.arange(137*96,dtype=np.float32).reshape(137,96)
    np.save(path,raw)
    for length in [1,7,25,100,137,200]:
        actual=FalsePositiveWindows(path,length)
        expected=[raw[i:i+length] for i in range(0,len(raw)-length,1)]
        assert len(actual)==len(expected)
        for i,reference in enumerate(expected):
            features,label=actual[i]
            np.testing.assert_array_equal(features,reference)
            assert label==0 and label.dtype==np.float32
            features[0,0]=-1
            assert actual[i][0][0,0]==reference[0,0] # Writable independent batches; backing data unchanged.
        try:actual[len(actual)]
        except IndexError:pass
        else:raise AssertionError('out-of-bounds window accepted')
    dataset=FalsePositiveWindows(path,25)
    original=[raw[i:i+25] for i in range(0,len(raw)-25,1)]
    whole=sum(window[0,0]>5000 for window in original)
    batched=sum(sum(dataset[i][0][0,0]>5000 for i in range(start,min(start+16,len(dataset)))) for start in range(0,len(dataset),16))
    assert batched==whole
print('PASS: exact original window coverage, all boundary sizes, writable independent windows, identical batched false-positive counts')
