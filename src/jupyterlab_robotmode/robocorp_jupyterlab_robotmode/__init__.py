
import json
import os.path as osp

from ._version import __version__

here = osp.abspath(osp.dirname(__file__))

with open(osp.join(here, 'labextension', 'package.json')) as fid:
    data = json.load(fid)


def _jupyter_labextension_paths():
    return [{
        'src': 'labextension',
        'dest': data['name']
    }]
