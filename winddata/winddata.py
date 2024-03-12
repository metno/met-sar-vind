""" License: This file is part of https://github.com/metno/met-sar-vind
             met-sar-vind is licensed under the Apache-2.0 license
             (https://github.com/metno/met-sar-vind/blob/main/LICENSE).
"""
import sys
from owslib import fes
from owslib.csw import CatalogueServiceWeb
from siphon.catalog import TDSCatalog


class WINDdata():
    """
     A class for getting Arome-Arctic wind data from thredds.

     Parameters
     -----------
     endpoint : str
               URL to Data. Default: https://csw.s-enda-dev.k8s.met.no/csw

     bbox     : int list
               A boudary box for search area speified in latitude and longitude
               bbox = [lon_min, lat_min, lon_max, lat_max]

               Example:
               >>> bbox = [-10, 75, 40, 85]

     start    : datetime object
               Specify the start time of the range to serchthe

     stop     :  datetime object
               Specify the stop time of the range to search

               Example:
               >>> from datetime import datetime, timedelta
               >>> stop = datetime(2010, 1, 1, 12, 30, 59).replace(tzinfo=pytz.utc)
               >>> start = stop - timedelta(days=7)

     kw_names : str
               A search string filter to limit the result of a search.
               Example:
               >>> kw_name='Arome-Arctic%'
    """

    def __init__(self, endpoint='https://csw.s-enda-dev.k8s.met.no/csw',
                 bbox=None, start=None, stop=None,
                 kw_names='Arome-Arctic%', crs='urn:ogc:def:crs:OGC:1.3:CRS84', *args, **kwargs):
        super(WINDdata, self).__init__(*args, **kwargs)
        constraints = []
        csw = None
        try:
            # connect
            csw = self._get_csw_connection(endpoint)
        except Exception as e:
            print("Exception: %s" % str(e))

        if kw_names:
            freetxt_filt = self._get_freetxt_search(kw_names)
            constraints.append(freetxt_filt)

        if all(v is not None for v in [start, stop]):
            begin, end = self._fes_date_filter(start, stop)
            constraints.append(begin)
            constraints.append(end)

        # if bbox:
            # bbox_crs = fes.BBox(bbox, crs=crs)
            # constraints.append(bbox_crs)

        if len(constraints) >= 2:
            filter_list = [fes.And(constraints)]
        else:
            filter_list = constraints

        self._get_csw_records(csw, filter_list, pagesize=10, maxrecords=100)
        self.csw = csw
        url_opendap = []

        for key, value in list(csw.records.items()):
            for ref in value.references:
                if ref['scheme'] == 'OPENDAP:OPENDAP':
                    if ref['url'].find('arome_arctic_det_2_5km') > 0:
                        url_opendap.append(ref['url'])

        self.url_opendap = url_opendap

    def _get_csw_connection(self, endpoint):
        csw = CatalogueServiceWeb(endpoint, timeout=60)
        return csw

    def _get_freetxt_search(self, kw_names):
        """
        Retuns a CSW search object based on input string(s)
        """
        freetxt_filt = fes.PropertyIsLike('csw:AnyText', literal=('%s' % kw_names),
                                          escapeChar='\\', singleChar='_',
                                          wildCard='%', matchCase=True)
        return freetxt_filt

    def _get_csw_records(self, csw, filter_list, pagesize=10, maxrecords=100):
        """
        Iterate `maxrecords`/`pagesize` times until the requested value in
        `maxrecords` is reached.
        """
        # Iterate over sorted results.
        csw_records = {}
        startposition = 0
        nextrecord = getattr(csw, "results", 1)
        while nextrecord != 0:
            csw.getrecords2(constraints=filter_list, startposition=startposition, maxrecords=pagesize, outputschema="http://www.opengis.net/cat/csw/2.0.2", esn='full',)
            csw_records.update(csw.records)
            if csw.results["nextrecord"] == 0:
                break
            startposition += pagesize + 1  # Last one is included.
            if startposition >= maxrecords:
                break
        csw.records.update(csw_records)

    def _fes_date_filter(self, start, stop, constraint="within"):
        """
        Take datetime-like objects and returns a fes filter for date range
        (begin and end inclusive).
        NOTE: Truncates the minutes!!!
        """

        start = start.strftime("%Y-%m-%d %H:00")
        stop = stop.strftime("%Y-%m-%d %H:00")
        if constraint == "overlaps":
            propertyname = "apiso:TempExtent_begin"
            begin = fes.PropertyIsLessThanOrEqualTo(propertyname=propertyname, literal=stop)
            propertyname = "apiso:TempExtent_end"
            end = fes.PropertyIsGreaterThanOrEqualTo(
                propertyname=propertyname, literal=start
            )
        elif constraint == "within":
            propertyname = "apiso:TempExtent_begin"
            begin = fes.PropertyIsGreaterThanOrEqualTo(
                propertyname=propertyname, literal=start
            )
            propertyname = "apiso:TempExtent_end"
            end = fes.PropertyIsLessThanOrEqualTo(propertyname=propertyname, literal=stop)
        else:
            raise NameError("Unrecognized constraint {}".format(constraint))
        return begin, end

    def _get_arome_time(self, hour):
        # Get closest %3 hours back in time from input hours
        hour = int(hour)
        if hour%3 == 2:
            time_closest = '%02d' % (hour + (3-hour%3))
        else:
            time_closest = '%02d' % (hour - hour%3)
        return time_closest

    def _get_aromeURL(self, year, month, day, hour):
        catalogUrl = 'https://thredds.met.no/thredds/catalog/aromearcticarchive/' \
            '{}/{}/{}/catalog.html'.format(year, month, day)
        cat = TDSCatalog(catalogUrl)
        aromeURL = ''
        hour = self._get_arome_time(hour)  # Find closest mode in time 00,03, 06, 09, ...
        aromeFile = 'arome_arctic_det_2_5km_{}{}{}T{}Z.nc'.format(year, month,
                                                                  day, hour)
        for dataset_name in list(cat.datasets):
            if dataset_name == aromeFile:
                dataset = cat.datasets[dataset_name]
                aromeURL = dataset.access_urls['OPENDAP']
        return aromeURL
