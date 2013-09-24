from sqlalchemy.exc import IntegrityError

from ichnaea.models import (
    Cell,
    CellMeasure,
    RADIO_TYPE,
    Wifi,
    WifiBlacklist,
    WifiMeasure,
)
from ichnaea.decimaljson import decode_datetime
from ichnaea.tasks import DatabaseTask
from ichnaea.worker import celery


def create_cell_measure(measure_data, entry):
    return CellMeasure(
        measure_id=measure_data['id'],
        created=decode_datetime(measure_data.get('created', '')),
        lat=measure_data['lat'],
        lon=measure_data['lon'],
        time=decode_datetime(measure_data.get('time', '')),
        accuracy=measure_data.get('accuracy', 0),
        altitude=measure_data.get('altitude', 0),
        altitude_accuracy=measure_data.get('altitude_accuracy', 0),
        mcc=entry['mcc'],
        mnc=entry['mnc'],
        lac=entry.get('lac', 0),
        cid=entry.get('cid', 0),
        psc=entry.get('psc', 0),
        asu=entry.get('asu', 0),
        signal=entry.get('signal', 0),
        ta=entry.get('ta', 0),
    )


def update_cell_measure_count(measure, session):
    if (measure.radio == -1 or measure.lac == 0 or measure.cid == 0):
        # only update data for complete records
        return

    # do we already know about these cells?
    query = session.query(Cell)
    query = query.filter(Cell.radio == measure.radio)
    query = query.filter(Cell.mcc == measure.mcc)
    query = query.filter(Cell.mnc == measure.mnc)
    query = query.filter(Cell.lac == measure.lac)
    query = query.filter(Cell.cid == measure.cid)
    cell = query.first()
    if cell:
        cell.new_measures = Cell.new_measures + 1
        cell.total_measures = Cell.total_measures + 1
    else:
        cell = Cell(radio=measure.radio, mcc=measure.mcc, mnc=measure.mnc,
                    lac=measure.lac, cid=measure.cid,
                    new_measures=1, total_measures=1)
        session.add(cell)


@celery.task(base=DatabaseTask, ignore_result=True)
def insert_cell_measure(measure_data, entries):
    cell_measures = []
    try:
        with insert_cell_measure.db_session() as session:
            for entry in entries:
                cell = create_cell_measure(measure_data, entry)
                # use more specific cell type or
                # fall back to less precise measure
                if entry.get('radio'):
                    cell.radio = RADIO_TYPE.get(entry['radio'], -1)
                else:
                    cell.radio = measure_data['radio']
                update_cell_measure_count(cell, session)
                cell_measures.append(cell)
            session.add_all(cell_measures)
            session.commit()
        return len(cell_measures)
    except IntegrityError as exc:  # pragma: no cover
        # TODO log error
        return 0
    except Exception as exc:  # pragma: no cover
        raise insert_cell_measure.retry(exc=exc)


def convert_frequency(entry):
    freq = entry.pop('frequency', 0)
    # if no explicit channel was given, calculate
    if freq and not entry['channel']:
        if 2411 < freq < 2473:
            # 2.4 GHz band
            entry['channel'] = (freq - 2407) // 5
        elif 5169 < freq < 5826:
            # 5 GHz band
            entry['channel'] = (freq - 5000) // 5


def update_wifi_measure_count(wifi_key, wifis, session):
    # side-effect, modifies wifis
    if wifi_key in wifis:
        wifi = wifis[wifi_key]
        wifi.new_measures = Wifi.new_measures + 1
        wifi.total_measures = Wifi.total_measures + 1
    else:
        wifis[wifi_key] = wifi = Wifi(
            key=wifi_key, new_measures=1, total_measures=1)
        session.add(wifi)


def create_wifi_measure(measure_data, entry):
    return WifiMeasure(
        measure_id=measure_data['id'],
        created=decode_datetime(measure_data.get('created', '')),
        lat=measure_data['lat'],
        lon=measure_data['lon'],
        time=decode_datetime(measure_data.get('time', '')),
        accuracy=measure_data.get('accuracy', 0),
        altitude=measure_data.get('altitude', 0),
        altitude_accuracy=measure_data.get('altitude_accuracy', 0),
        id=entry.get('id', None),
        key=entry['key'],
        channel=entry.get('channel', 0),
        signal=entry.get('signal', 0),
    )


@celery.task(base=DatabaseTask, ignore_result=True)
def insert_wifi_measure(measure_data, entries):
    wifi_measures = []
    wifi_keys = set([e['key'] for e in entries])
    try:
        with insert_wifi_measure.db_session() as session:
            # did we get measures for blacklisted wifis?
            blacked = session.query(WifiBlacklist.key).filter(
                WifiBlacklist.key.in_(wifi_keys)).all()
            blacked = set([b[0] for b in blacked])
            # do we already know about these wifis?
            wifis = session.query(Wifi.key, Wifi).filter(
                Wifi.key.in_(wifi_keys))
            wifis = dict(wifis.all())
            for entry in entries:
                wifi_key = entry['key']
                # skip blacklisted wifi AP's
                if wifi_key in blacked:
                    continue
                # convert frequency into channel numbers and remove frequency
                convert_frequency(entry)
                wifi_measures.append(create_wifi_measure(measure_data, entry))
                # update new/total measure counts
                update_wifi_measure_count(wifi_key, wifis, session)

            session.add_all(wifi_measures)
            session.commit()
        return len(wifi_measures)
    except IntegrityError as exc:
        # TODO log error
        return 0
    except Exception as exc:  # pragma: no cover
        raise insert_wifi_measure.retry(exc=exc)
