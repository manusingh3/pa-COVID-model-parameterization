# script that reads WorldPop tiff files and populates the exposure file
import os
import datetime
import itertools
import getpass
import argparse
import logging
from pathlib import Path

import geopandas as gpd
from rasterstats import zonal_stats

import utils

INPUT_DIR = 'Inputs'
SHAPEFILE_DIR = 'Shapefiles'
DIR_PATH = os.path.dirname(os.path.realpath(__file__))

CONFIG_FILE = 'config.yml'
OUTPUT_DIR = os.path.join(DIR_PATH, 'Outputs', '{}', 'Exposure_SADD')
OUTPUT_GEOJSON = '{}_Exposure.geojson'

GENDER_CLASSES = ["f","m"]
AGE_CLASSES = [0,1,5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80]
WORLDPOP_DIR = 'WorldPop'
WORLDPOP_FILENAMES = {
    'sadd':  '{country_iso3}_{gender}_{age}_2020.tif',
    'pop': '{country_iso3}_ppp_2020.tif',
    'unadj': '{country_iso3}_ppp_2020_UNadj.tif'
}
WORLDPOP_URL = {
    'age_sex': 'ftp://ftp.worldpop.org.uk/GIS/AgeSex_structures/Global_2000_2020/2020/{0}/{1}_{2}_{3}_2020.tif',
    'pop': 'ftp://ftp.worldpop.org.uk/GIS/Population/Global_2000_2020/2020/{0}/{1}_ppp_2020.tif',
    'unadj': 'ftp://ftp.worldpop.org.uk/GIS/Population/Global_2000_2020/2020/{0}/{1}_ppp_2020_UNadj.tif'
}


utils.config_logger()
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('country_iso3',
                        help='Country ISO3. Options are: afg')
    parser.add_argument('-d', '--download', action='store_true',
                        help='Download the WorldPop data -- required upon first run')
    return parser.parse_args()


def main(country_iso3, download_worldpop=False):

    # Get config file
    config = utils.parse_yaml(CONFIG_FILE)[country_iso3]

    # Get input boundary shape file
    input_dir = os.path.join(DIR_PATH, INPUT_DIR, country_iso3)
    input_shp = os.path.join(input_dir, SHAPEFILE_DIR, config['admin']['directory'], config['admin']['filename'])
    ADM2boundaries = gpd.read_file(input_shp)

    # Download the worldpop data
    if download_worldpop:
        get_worldpop_data(country_iso3, input_dir)

    # gender and age groups
    gender_age_groups = list(itertools.product(GENDER_CLASSES, AGE_CLASSES))
    for gender_age_group in gender_age_groups:
        gender_age_group_name = f'{gender_age_group[0]}_{gender_age_group[1]}'
        logger.info(f'analyising gender age {gender_age_group_name}')
        input_tif_file = os.path.join(input_dir, WORLDPOP_DIR,
                                      WORLDPOP_FILENAMES['sadd'].format(country_iso3=country_iso3.lower(),
                                                                        gender=gender_age_group[0],
                                                                        age=gender_age_group[1]))
        zs = zonal_stats(input_shp, input_tif_file, stats='sum')
        total_pop=[district_zs.get('sum') for district_zs in zs]
        ADM2boundaries[gender_age_group_name]=total_pop

    # total population for cross check
    logger.info('Cross-checking with total pop')
    input_tiff_pop = os.path.join(input_dir, WORLDPOP_DIR,
                                  WORLDPOP_FILENAMES['pop'].format(country_iso3=country_iso3.lower()))
    zs = zonal_stats(input_shp, input_tiff_pop,stats='sum')
    total_pop=[district_zs.get('sum') for district_zs in zs]
    ADM2boundaries['tot_pop']=total_pop

    # total population UNadj for cross check
    logger.info('Cross-checking with UNadj total pop')
    input_tiff_pop_unadj = os.path.join(input_dir, WORLDPOP_DIR,
                                        WORLDPOP_FILENAMES['unadj'].format(country_iso3=country_iso3.lower()))
    zs = zonal_stats(input_shp, input_tiff_pop_unadj, stats='sum')
    total_pop=[district_zs.get('sum') for district_zs in zs]
    ADM2boundaries['tot_pop_UNadj']=total_pop

    # total from disaggregated
    logger.info('Getting totals from disaggregated')
    columns_to_sum=['{}_{}'.format(gender_age_group[0],gender_age_group[1]) for gender_age_group in gender_age_groups]
    ADM2boundaries['tot_sad']=ADM2boundaries.loc[:,columns_to_sum].sum(axis=1)

    # adding manually Kochi nomads
    if 'kochi' in config:
        logger.info('Adding Kuchi')
        ADM1_kuchi = config['kochi']['adm1']
        # total population in these provinces
        pop_in_kuchi_ADM1=ADM2boundaries[ADM2boundaries['ADM1_PCODE'].isin(ADM1_kuchi)]['tot_sad'].sum()
        for row_index, row in ADM2boundaries.iterrows():
            if row['ADM1_PCODE'] in ADM1_kuchi:
                tot_kuchi_in_ADM2=0
                for gender_age_group in gender_age_groups:
                    # population weighted
                    gender_age_group_name = f'{gender_age_group[0]}_{gender_age_group[1]}'
                    kuchi_pp=config['kochi']['total']*(row[gender_age_group_name]/pop_in_kuchi_ADM1)
                    ADM2boundaries.loc[row_index,gender_age_group_name]=row[gender_age_group_name]+kuchi_pp
                    tot_kuchi_in_ADM2+=kuchi_pp
                ADM2boundaries.loc[row_index,'kuchi']=tot_kuchi_in_ADM2
                comment = f'Added in total {tot_kuchi_in_ADM2} Kuchi nomads to WorldPop estimates'
                ADM2boundaries.loc[row_index,'comment'] = comment

    # Write to file
    ADM2boundaries['created_at'] = str(datetime.datetime.now())
    ADM2boundaries['created_by'] = getpass.getuser()
    output_dir = OUTPUT_DIR.format(country_iso3)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    output_geojson = os.path.join(output_dir, OUTPUT_GEOJSON.format(country_iso3))
    logger.info(f'Writing to file {output_geojson}')
    ADM2boundaries.to_file(output_geojson, driver='GeoJSON')


def get_worldpop_data(country_iso3, input_dir):
    output_dir = os.path.join(input_dir, WORLDPOP_DIR)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for age in AGE_CLASSES:
        for gender in GENDER_CLASSES:
            url = WORLDPOP_URL['age_sex'].format(country_iso3.upper(), country_iso3.lower(), gender, age)
            utils.download_ftp(url, os.path.join(output_dir, url.split('/')[-1]))
    for pop_type in ['pop', 'unadj']:
        url = WORLDPOP_URL[pop_type].format(country_iso3.upper(), country_iso3.lower())
        utils.download_ftp(url, os.path.join(output_dir, url.split('/')[-1]))


if __name__ == '__main__':
    args = parse_args()
    main(args.country_iso3.upper(), download_worldpop=args.download)
