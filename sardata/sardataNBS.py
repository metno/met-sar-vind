""" License: This file is part of https://github.com/metno/met-sar-vind
             met-sar-vind is licensed under the Apache-2.0 license
             (https://github.com/metno/met-sar-vind/blob/main/LICENSE).
"""
from owslib import fes
from owslib.csw import CatalogueServiceWeb
from siphon.catalog import TDSCatalog
from netCDF4 import Dataset


class SARData():
    """
     A class for getting Sentinel-1 netCDF data from the Norwegian Ground Segment (NBS)

     Parameters
     -----------
     endpoint : str
               URL to NBS. Default: https://nbs.csw.met.no/csw

     bbox     : int list
               A boudary box for search area speified in latitude and longitude
               bbox = [lon_min, lat_min, lon_max, lat_max]

               Example:
               >>> bbox = [-10, 75, 40, 85]

     start    : datetime object
               Specify the start time of the range to serchthe

     stop     :  datetime object
               Specify the stop time of the range to serchthe

               Example:
               >>> from datetime import datetime, timedelta
               >>> stop = datetime(2010, 1, 1, 12, 30, 59).replace(tzinfo=pytz.utc)
               >>> start = stop - timedelta(days=7)

     kw_names : str
               A search string filter to limit the result of a search.
               Example:
               >>> kw_name='S1A_EW_GRDM_1SDH%'

    """

    def __init__(self, endpoint='https://nbs.csw.met.no/csw', bbox=None, start=None, stop=None,
                 kw_names=None, crs='urn:ogc:def:crs:OGC:1.3:CRS84', *args, **kwargs):
        super(SARData, self).__init__(*args, **kwargs)
        constraints = []
        csw = None

        # connect to endpoint
        try:
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

        if bbox:
            bbox_crs = fes.BBox(bbox, crs=crs)
            constraints.append(bbox_crs)
        if len(constraints) >= 2:
            filter_list = [fes.And(constraints)]
        else:
            filter_list = constraints

        self._get_csw_records(csw, filter_list, pagesize=10, maxrecords=100)
        self.csw = csw
        url_opendap = []

        for key, value in list(csw.records.items()):
            for ref in value.references:
                if ref['scheme'] == 'OPeNDAP:OPeNDAP':
                    url_opendap.append(ref['url'])
        self.url_opendap = url_opendap

    def _get_csw_connection(self, endpoint):
        """ Connect to CSW server
        """
        csw = CatalogueServiceWeb(endpoint, timeout=60)
        return csw

    def _get_freetxt_search(self, kw_names):
        """
        Retuns a CSW search object based on input string
        """
        freetxt_filt = fes.PropertyIsLike('csw:AnyText',  literal=('%s' % kw_names),
                                          escapeChar='\\', singleChar='?',
                                          wildCard='%', matchCase=True)
        return freetxt_filt

    def _get_csw_records(self, csw, filter_list, pagesize=10, maxrecords=100):
        """
        Iterate `maxrecords`/`pagesize` times until the requested value in
        `maxrecords` is reached.
        """
        csw_records = {}
        startposition = 0
        nextrecord = getattr(csw, "results", 1)
        while nextrecord != 0:
            csw.getrecords2(
                constraints=filter_list,
                startposition=startposition,
                maxrecords=pagesize,
                outputschema="http://www.opengis.net/cat/csw/2.0.2",
                esn='full',
            )
            csw_records.update(csw.records)
            if csw.results["nextrecord"] == 0:
                break
            startposition += pagesize + 1  # Last one is included.
            if startposition >= maxrecords:
                break
        csw.records.update(csw_records)

    def _fes_date_filter(self, start, stop, constraint="overlaps"):
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

    def _get_datasets(satellite, year, month, day):
        """ Get list of all availible data sets"""
        catalogUrl = 'http://nbstds.met.no/thredds/catalog/NBS/' \
            '{}/{}/{}/{}/EW/catalog.xml'.format(satellite, year, month, day)
        cat = TDSCatalog(catalogUrl)
        return list(cat.datasets), catalogUrl

    def open_dataset(dataset_name, tdsCatalogUrl):
        """
        Open and return a netCDF Dataset object for a given date and image index
        of Sentinel-2 data from THREDDS nbs server.
        """
        cat = TDSCatalog(tdsCatalogUrl)
        dataset = cat.datasets[dataset_name]
        ds = Dataset(dataset.access_urls['OPENDAP'])
        return ds
