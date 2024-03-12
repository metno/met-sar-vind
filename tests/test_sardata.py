import pytest
import datetime

from sardata.sardataNBS import SARData
import pytz
from owslib import fes
from unittest import mock


@pytest.mark.unittests
@pytest.mark.sardata
def testSARData_input_parameter():
    """ Test setting up CSW cronstrains from input parameters
    """
    """ Freetext search """
    kw_names = 'S1A%'
    or_filt = SARData._get_freetxt_search([], kw_names)
    assert type(or_filt) == fes.PropertyIsLike

    """ Restricting search from start,stop time """
    stop = datetime.datetime(2022, 1, 8, 5, 30, 59).replace(tzinfo=pytz.utc)
    start = stop - datetime.timedelta(days=1)
    begin, end = SARData._fes_date_filter([], start, stop)
    assert type(begin) == fes.PropertyIsGreaterThanOrEqualTo
    assert type(end) == fes.PropertyIsLessThanOrEqualTo

    """ Restricting search from boundering box """
    bbox = [-10, 75, 40, 85]
    crs = 'urn:ogc:def:crs:OGC:1.3:CRS84'
    bbox_crs = fes.BBox(bbox, crs=crs)
    assert type(bbox_crs) == fes.BBox


@pytest.mark.sardata
@mock.patch("sardata.sardataNBS.SARData._get_csw_records")
@mock.patch("sardata.sardataNBS.SARData._get_csw_connection")
def testSARData_from_NBS(mock_csw, mock_get_csw_records):
    """ Test using CSW for reading Sentinel-1 data.
        This test requres access to https://nbs.csw.met.no/csw
    """
    class Mycsw_class:
        records = {}

    class Myval_class:
        references = []

    myrec_val = Myval_class
    myrec_val.references = [{'scheme': 'OPeNDAP:OPeNDAP',
                             'url': 'https://nbstds.met.no/thredds/dodsC/NBS/S2B/2022/02/08/'\
                             'S2B_MSIL1C_20220208T103109_N0400_R108_T34WEB_20220208T125524.nc'},
                            {'scheme': 'OGC:WMS',
                             'url': 'https://nbswms.met.no/thredds/wms_ql/NBS/S2B/2022/02/08/'\
                             'S2B_MSIL1C_20220208T103109_N0400_R108_T34WEB_20220208T125524.nc\
                             ?SERVICE=WMS&REQUEST=GetCapabilities'},
                            {'scheme': 'download',
                             'url': 'https://nbstds.met.no/thredds/fileServer/NBS/S2B/2022/02/08/'\
                             'S2B_MSIL1C_20220208T103109_N0400_R108_T34WEB_20220208T125524.nc'},
                            {'scheme': None,
                             'url': "https://colhub.met.no/odata/v1/Products('\
                             '2a34c82f-675a-4304-af4b-b1c3d8030824')/$value"}]
    mycsw = Mycsw_class
    mycsw.records = {'key1': myrec_val}

    mock_csw.return_value = mycsw()
    mock_get_csw_records.return_value = mycsw()
    for (key, val) in list(mycsw.records.items()):
        sw = SARData()
        print(sw.url_opendap[0])
        assert sw.url_opendap[0] == 'https://nbstds.met.no/thredds/dodsC/NBS/S2B/2022/02/08/'\
                'S2B_MSIL1C_20220208T103109_N0400_R108_T34WEB_20220208T125524.nc'
