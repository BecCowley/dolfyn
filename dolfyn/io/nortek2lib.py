# Branch read_signature-cython has the attempt to do this in Cython.

from __future__ import print_function
import struct
import os.path as path
import numpy as np
import warnings
from ..data import time


def reduce_by_average(data, ky0, ky1):
    # Average two arrays together, if they both exist.
    if ky1 in data:
        tmp = data.pop_data(ky1)
        if ky0 in data:
            data[ky0] += tmp
            data[ky0] /= 2
        else:
            data[ky0] = tmp


def reduce_by_average_angle(data, ky0, ky1, degrees=True):
    # Average two arrays of angles together, if they both exist.
    if degrees:
        rad_fact = np.pi / 180
    else:
        rad_fact = 1
    if ky1 in data:
        if ky0 in data:
            data[ky0] = np.angle(
                np.exp(1j * data.pop_data(ky0) * rad_fact) +
                np.exp(1j * data.pop_data(ky1) * rad_fact)) / rad_fact
        else:
            data[ky0] = data.pop_data(ky1)


# This is the data-type of the index file.
# This must match what is written-out by the create_index function.
index_dtype = np.dtype([('ens', np.uint64),
                        ('pos', np.uint64),
                        ('ID', np.uint16),
                        ('config', np.uint16),
                        ('beams_cy', np.uint16),
                        ('_blank', np.uint16),
                        ('year', np.uint8),
                        ('month', np.uint8),
                        ('day', np.uint8),
                        ('hour', np.uint8),
                        ('minute', np.uint8),
                        ('second', np.uint8),
                        ('usec100', np.uint16),
                        ])

hdr = struct.Struct('<BBBBhhh')


def calc_time(year, month, day, hour, minute, second, usec):
    dt = np.empty(year.shape, dtype='O')
    for idx, (y, mo, d, h, mi, s, u) in enumerate(
            zip(year, month, day,
                hour, minute, second, usec)):
        # Note that month is zero-based
        dt[idx] = time.datetime(y, mo + 1, d, h, mi, s, u)
    return time.time_array(time.date2num(dt))


def create_index_slow(infile, outfile, N_ens):
    fin = open(infile, 'rb')
    fout = open(outfile, 'wb')
    ens = 0
    N = 0
    config = 0
    last_ens = -1
    while N < N_ens:
        pos = fin.tell()
        try:
            dat = hdr.unpack(fin.read(hdr.size))
        except:
            break
        if dat[2] in [21, 24, 26]:
            fin.seek(2, 1)
            config = struct.unpack('<H', fin.read(2))[0]
            fin.seek(4, 1)
            yr, mo, dy, h, m, s, u = struct.unpack('6BH', fin.read(8))
            fin.seek(14, 1)
            beams_cy = struct.unpack('<H', fin.read(2))[0]
            fin.seek(40, 1)
            ens = struct.unpack('<I', fin.read(4))[0]
            if last_ens > 0 and last_ens != ens:
                N += 1
            fout.write(struct.pack('<QQ4H6BH', N, pos, dat[2],
                                   config, beams_cy, 0,
                                   yr, mo, dy, h, m, s, u))
            fin.seek(dat[4] - 76, 1)
            last_ens = ens
        else:
            fin.seek(dat[4], 1)
        # if N < 5:
        #     print('%10d: %02X, %d, %02X, %d, %d, %d, %d\n' %
        #           (pos, dat[0], dat[1], dat[2], dat[4],
        #            N, ens, last_ens))
        # else:
        #     break
    fin.close()
    fout.close()


def get_index(infile, reload=False):
    index_file = infile + '.index'
    if not path.isfile(index_file) or reload:
        print("Indexing...", end='')
        create_index_slow(infile, index_file, 2 ** 32)
        print(" Done.")
    # else:
    #     print("Using saved index file.")
    return np.fromfile(index_file, dtype=index_dtype)


def index2ens_pos(index):
    """Condense the index to only be the first occurence of each
    ensemble. Returns only the position (the ens number is the array
    index).
    """
    dens = np.ones(index['ens'].shape, dtype='bool')
    dens[1:] = np.diff(index['ens']) != 0
    return index['pos'][dens]


def getbit(val, n):
    return bool((val >> n) & 1)


def headconfig_int2dict(val):
    """Convert the burst Configuration bit-mask to a dict of bools.
    """
    return dict(
        press_valid=getbit(val, 0),
        temp_valid=getbit(val, 1),
        compass_valid=getbit(val, 2),
        tilt_valid=getbit(val, 3),
        # bit 4 is unused
        vel=getbit(val, 5),
        amp=getbit(val, 6),
        corr=getbit(val, 7),
        alt=getbit(val, 8),
        alt_raw=getbit(val, 9),
        ast=getbit(val, 10),
        echo=getbit(val, 11),
        ahrs=getbit(val, 12),
        p_gd=getbit(val, 13),
        std=getbit(val, 14),
        # bit 15 is unused
    )


def beams_cy_int2dict(val, id):
    """Convert the beams/coordinate-system bytes to a dict of values.
    """
    if id == 28:  # 0x1C (echosounder)
        return dict(ncells=val)
    return dict(
        ncells=val & (2 ** 10 - 1),
        cy=['ENU', 'XYZ', 'BEAM', None][val >> 10 & 3],
        nbeams=val >> 12
    )


def isuniform(vec):
    return np.all(vec == vec[0])


def collapse(vec, name=None):
    """Check that the input vector is uniform, then collapse it to a
    single value, otherwise raise a warning.
    """
    if name is None:
        name = '**unkown**'
    if not isuniform(vec):
        warnings.warn("The variable {} is expected to be uniform,"
                      " but it is not.".format(name))
        return vec
    return vec[0]


def calc_config(index):
    """Calculate the configuration information (e.g., number of pings,
    number of beams, struct types, etc.) from the index data.

    Returns
    =======
    config : dict
        A dict containing the key information for initializing arrays.
    """
    ids = np.unique(index['ID'])
    config = {}
    for id in ids:
        if id not in [21, 24, 26]:
            continue
        inds = index['ID'] == id
        _config = index['config'][inds]
        _beams_cy = index['beams_cy'][inds]
        # Check that these variables are consistent
        if not isuniform(_config):
            raise Exception("config are not identical for id: 0x{:X}."
                            .format(id))
        if not isuniform(_beams_cy):
            raise Exception("beams_cy are not identical for id: 0x{:X}."
                            .format(id))
        # Now that we've confirmed they are the same:
        config[id] = headconfig_int2dict(_config[0])
        config[id].update(beams_cy_int2dict(_beams_cy[0], id))
        config[id]['_config'] = _config[0]
        config[id]['_beams_cy'] = _beams_cy[0]
        config[id].pop('cy')
    return config