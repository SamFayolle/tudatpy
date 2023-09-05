from ..kernel.numerical_simulation import environment_setup  # type:ignore
from ..kernel.numerical_simulation import estimation, environment  # type:ignore
from ..kernel.numerical_simulation.estimation_setup import observation  # type:ignore

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import datetime

from astroquery.mpc import MPC
from astropy.time import Time
import astropy

from typing import Union, Tuple, List, Dict


class BatchMPC:
    def __init__(self) -> None:
        self._table: pd.DataFrame = pd.DataFrame()
        self._observatories: List[str] = []
        self._space_telescopes: List[str] = []
        self._bands: List[str] = []
        self._MPC_codes: List[str] = []
        self._size: int = 0

        self._observatory_info: Union[pd.DataFrame, None] = None
        self._MPC_space_telescopes: List[str] = []

        self._get_station_info()
        self._add_observatory_positions()

        self._epoch_start: float = 0.0
        self._epoch_end: float = 0.0

        # for manual additions of table (from_pandas, from_astropy)
        self._req_cols = ["number", "epoch", "RA", "DEC", "band", "observatory"]

    # getters to make everything read-only
    @property
    def table(self) -> pd.DataFrame:
        """Pandas dataframe with observation data"""
        return self._table

    @property
    def observatories(self) -> List[str]:
        """List of observatories in batch"""
        return self._observatories

    @property
    def space_telescopes(self) -> List[str]:
        """List of satellite_observatories in batch"""
        return self._space_telescopes

    @property
    def MPC_objects(self) -> List[str]:
        """List of MPC objects"""
        return self._MPC_codes

    @property
    def size(self) -> int:
        """Number of observations in batch"""
        return self._size

    @property
    def bands(self) -> List[str]:
        """List of bands in batch"""
        return self._bands

    @property
    def epoch_start(self) -> float:
        """Epoch of oldest observation in batch in seconds since J2000 TDB"""
        return self._epoch_start

    @property
    def epoch_end(self) -> float:
        """Epoch of latest observation in batch in seconds since J2000 TDB"""
        return self._epoch_end

    def __len__(self):
        return self._size

    def __add__(self, other):
        temp = BatchMPC()

        temp._table = (
            pd.concat([self._table, other._table])
            .sort_values("epoch")
            .drop_duplicates()
        )

        temp._refresh_metadata()

        return temp

    # helper functions
    def _refresh_metadata(self) -> None:
        """Update batch metadata"""
        self._table.drop_duplicates()

        self._observatories = list(self._table.observatory.unique())
        self._space_telescopes = [
            x for x in self._observatories if x in self._MPC_space_telescopes
        ]
        self._bands = list(self._table.band.unique())
        self._MPC_codes = list(self._table.number.unique())
        self._size = len(self._table)

        self._epoch_start = self._table.epochJ2000secondsTDB.min()
        self._epoch_end = self._table.epochJ2000secondsTDB.max()

    def _get_station_info(self) -> None:
        """Retrieve data on MPC listed observatories"""
        try:
            temp = MPC.get_observatory_codes().to_pandas()  # type: ignore
            # This query checks if Longitude is Nan: non-terretrial telescopes
            sats = list(temp.query("Longitude != Longitude").Code.values)
            self._observatory_info = temp
            self._MPC_space_telescopes = sats
        except Exception as e:
            print("An error occured while retrieving observatory data")
            print(e)

    def _add_observatory_positions(self) -> None:
        """Add observatory cartesian postions to station data"""
        temp = self._observatory_info

        if temp is None:
            # This error will probably never occur
            txt = """Observatory positions can not be assigned.
                  This is likely due to a failure in retrieving the observatories."""
            raise ValueError(txt)

        # TODO replace with tudat constant:
        r_earth = 6378137.0

        # Add geocentric cartesian positions
        temp = (
            temp.assign(X=lambda x: x.cos * r_earth * np.cos(np.radians(x.Longitude)))
            .assign(Y=lambda x: x.cos * r_earth * np.sin(np.radians(x.Longitude)))
            .assign(Z=lambda x: x.sin * r_earth)
        )
        self._observatory_info = temp

    # methods for data retrievels
    def get_observations(self, MPCcodes: List[int]) -> None:
        # TODO docstring
        for code in MPCcodes:
            try:
                obs = MPC.get_observations(code).to_pandas()  # type: ignore

                # convert JD to J2000 and UTC, convert deg to rad
                obs = (
                    obs.assign(
                        epochJ2000secondsTDB=lambda x: (
                            Time(x.epoch, format="jd", scale="utc").tdb.value
                            - 2451545.0
                        )
                        * 86400
                    )
                    .assign(RA=lambda x: np.radians(x.RA))
                    .assign(DEC=lambda x: np.radians(x.DEC))
                    .assign(epochUTC=lambda x: Time(x.epoch, format="jd").to_datetime())
                )

                # convert object mpc code to string
                obs["number"] = obs.number.astype(str)
                self._table = pd.concat([self._table, obs])

            except Exception as e:
                print(f"An error occured while retrieving observations of MPC: {code}")
                print(e)

        self._refresh_metadata()

    def _add_table(self, table: pd.DataFrame, in_degrees: bool = True):
        # TODO docstring
        obs = table
        obs = obs.assign(
            epochJ2000secondsTDB=lambda x: (
                Time(x.epoch, format="jd", scale="utc").tdb.value - 2451545.0
            )
            * 86400
        ).assign(epochUTC=lambda x: Time(x.epoch, format="jd").to_datetime())
        if in_degrees:
            obs = obs.assign(RA=lambda x: np.radians(x.RA)).assign(
                DEC=lambda x: np.radians(x.DEC)
            )

        # convert object mpc code to string
        obs["number"] = obs.number.astype(str)
        self._table = pd.concat([self._table, obs])
        self._refresh_metadata()

    def from_astropy(
        self, table: astropy.table.QTable, in_degrees: bool = True, frame: str = "J2000"
    ):
        # TODO docstring
        if not (
            isinstance(table, astropy.table.QTable)
            or isinstance(table, astropy.table.Table)
        ):
            raise ValueError(
                "Table must be of type astropy.table.QTable or astropy.table.Table"
            )
        if frame != "J2000":
            txt = "Only observations in J2000 are supported currently"
            raise NotImplementedError(txt)

        # check if all mandatory names are present
        if not set(self._req_cols).issubset(set(table.colnames)):
            txt = f"Table must include a set of mandatory columns: {self._req_cols}"
            raise ValueError(txt)

        self._add_table(table=table.to_pandas(), in_degrees=in_degrees)

    def from_pandas(
        self, table: pd.DataFrame, in_degrees: bool = True, frame: str = "J2000"
    ):
        # TODO docstring
        if not isinstance(table, pd.DataFrame):
            raise ValueError("Table must be of type pandas.DataFrame")
        if frame != "J2000":
            txt = "Only observations in J2000 are supported currently"
            raise NotImplementedError(txt)

        # check if all mandatory names are present
        if not set(self._req_cols).issubset(set(table.columns)):
            txt = f"Table must include a set of mandatory columns: {self._req_cols}"
            raise ValueError(txt)

        self._add_table(table=table, in_degrees=in_degrees)

    def filter(
        self,
        bands: Union[List[str], str, None] = None,
        observatories: Union[List[str], str, None] = None,
        observatories_exclude: Union[List[str], str, None] = None,
        epoch_start: Union[float, datetime.datetime, None] = None,
        epoch_end: Union[float, datetime.datetime, None] = None,
    ):
        # TODO docstring
        """Filter out observations from the batch

        Parameters
        ----------
        bands : Union[list, str, None], optional
            observation bands to include see MPC for details, by default None
        stations : Union[list, str, None], optional
            A list of stations to keep, by default None
        stations_exclude : Union[list, str, None], optional
            A list of stations to remove, by default None
        epoch_start : Union[float, datetime.datetime, None], optional
            Start date to include observations from, can be in python datetime in utc\
                 or the more conventional tudat seconds since j2000 in TDB if float,\
                     by default None
        epoch_end : Union[float, datetime.datetime, None], optional
            Final date to include observations from, can be in python datetime in utc\
                 or the more conventional tudat seconds since j2000 in TDB if float,\
                     by default None
        """

        # basic user input handling
        assert not isinstance(
            observatories, int
        ), "stations parameter must be of type 'str' or 'List[str]'"

        if isinstance(bands, str):
            bands = [bands]
        if isinstance(observatories, str):
            observatories = [observatories]

        if (observatories is not None) and (observatories_exclude is not None):
            txt = "Include or exclude observatories, not both at the same time."
            raise ValueError(txt)

        if bands is not None:
            self._table = self._table.query("band == @bands")
        if observatories is not None:
            self._table = self._table.query("observatory == @observatories")
        if observatories_exclude is not None:
            self._table = self._table.query("observatory != @observatories_exclude")
        if epoch_start is not None:
            if isinstance(epoch_start, float) or isinstance(epoch_start, int):
                self._table = self._table.query("epochJ2000secondsTDB >= @epoch_start")
            elif isinstance(epoch_start, datetime.datetime):
                self._table = self._table.query("epochUTC >= @epoch_start")
        if epoch_end is not None:
            if isinstance(epoch_end, float) or isinstance(epoch_end, int):
                self._table = self._table.query("epochJ2000secondsTDB <= @epoch_end")
            elif isinstance(epoch_end, datetime.datetime):
                self._table = self._table.query("epochUTC <= @epoch_end")

        self._refresh_metadata()

    def to_tudat(
        self,
        bodies: environment.SystemOfBodies,
        included_satellites: Union[Dict[str, str], None] = None,
        # ^ keys= MPC
        station_body: str = "Earth",
    ) -> Tuple[estimation.ObservationCollection, Dict[str, observation.LinkDefinition]]:
        # TODO docstring

        # start user input validation
        # Ensure that Earth is in the SystemOfBodies object
        try:
            bodies.get(station_body)
        except Exception as e:
            print(f"Body {station_body} is not in bodies")
            raise e

        # get satellites to include and exclude
        if included_satellites is not None:
            sat_obs_codes_included = list(included_satellites.keys())
            sat_obs_codes_excluded = list(
                set(self._space_telescopes) - set(sat_obs_codes_included)
            )

            # Ensure that the satellite is in the SystemOfBodies object
            for sat in list(included_satellites.values()):
                try:
                    bodies.get(sat)
                except Exception as e:
                    print(f"Body {sat} is not in bodies")
                    raise e
        else:
            sat_obs_codes_included = []
            sat_obs_codes_excluded = self._space_telescopes

        # end user input validation

        # get relevant stations positions
        tempStations = self._observatory_info.query("Code == @self.observatories").loc[
            :, ["Code", "X", "Y", "Z"]
        ]

        # add station positions to the observations
        observations_table = pd.merge(
            left=self._table,
            right=tempStations,
            left_on="observatory",
            right_on="Code",
            how="left",
        )
        # remove the observations from unused satellite observatories
        observations_table = observations_table.query("Code != @sat_obs_codes_excluded")

        # add asteroid bodies to SystemOfBodies object
        # TODO is there a better way to do this?
        # bodies map is not exposed
        for body in self._MPC_codes:
            try:
                bodies.get(body)
            except Exception as e:
                bodies.create_empty_body(str(body))

        # add ground stations to the earth body
        for idx in range(len(tempStations)):
            station_name = tempStations.iloc[idx].Code

            # skip if it is a satellite observatory
            if station_name in self._space_telescopes:
                continue

            ground_station_settings = environment_setup.ground_station.basic_station(
                station_name=station_name,
                station_nominal_position=[
                    tempStations.iloc[idx].X,
                    tempStations.iloc[idx].Y,
                    tempStations.iloc[idx].Z,
                ],
            )

            # Check if station already exists
            if station_name not in bodies.get(station_body).ground_station_list:
                # Add the ground station to the environment
                environment_setup.add_ground_station(
                    bodies.get_body(station_body), ground_station_settings
                )

        # get unique combinations of mpc bodies and observatories
        unique_link_combos = (
            observations_table.loc[:, ["number", "observatory"]].drop_duplicates()
        ).values

        linksDictionary = {}
        observation_set_list = []
        for combo in unique_link_combos:
            MPC_number = combo[0]
            station_name = combo[1]

            # TODO this should not happen, temporarily here for testing
            if station_name in sat_obs_codes_excluded:
                raise Exception

            # CREATE LINKS
            link_ends = dict()

            # observed body link
            link_ends[observation.transmitter] = observation.body_origin_link_end_id(
                MPC_number
            )

            if station_name in sat_obs_codes_included:
                # link for a satellite
                sat_name = included_satellites[station_name]
                link_ends[observation.receiver] = observation.body_origin_link_end_id(
                    sat_name
                )
                link_definition = observation.link_definition(link_ends)
                linksDictionary[f"{MPC_number}_{sat_name}"] = link_definition
            else:
                # link for a ground station
                link_ends[
                    observation.receiver
                ] = observation.body_reference_point_link_end_id(
                    station_body, station_name
                )
                link_definition = observation.link_definition(link_ends)
                linksDictionary[f"{MPC_number}_{station_name}"] = link_definition

            # get observations, angles and times for this specific link
            observations_for_this_link = observations_table.query(
                "number == @MPC_number"
            ).query("observatory == @station_name")

            observation_angles = observations_for_this_link.loc[
                :, ["RA", "DEC"]
            ].to_numpy()

            observation_times = observations_for_this_link.loc[
                :, ["epochJ2000secondsTDB"]
            ].to_numpy()[:, 0]

            # create a set of obs for this link
            observation_set = estimation.single_observation_set(
                observation.angular_position_type,
                link_definition,
                observation_angles,
                observation_times,
                observation.receiver,
            )

            observation_set_list.append(observation_set)

        observation_collection = estimation.ObservationCollection(observation_set_list)
        return observation_collection, linksDictionary

    def plot_observations(self, objects=None, projection="aitoff"):
        fig, ax = plt.subplots(
            1, 1, subplot_kw={"projection": projection}, figsize=(15, 7)
        )

        if objects is None:
            objs = self.MPC_objects
        else:
            objs = objects

        markers = ["o", "+", "^"]
        for idx, obj in enumerate(objs):
            tab = self._table.query("number == @obj")

            a = plt.scatter(
                np.unwrap(tab.RA),
                np.unwrap(tab.DEC),
                s=5,
                marker=markers[int(idx % len(markers))],
                c=tab.epochJ2000secondsTDB,
                cmap=cm.Spectral,
                label=obj,
                vmin=self._table.query("number == @objs").epochJ2000secondsTDB.min(),
                vmax=self._table.query("number == @objs").epochJ2000secondsTDB.max(),
            )

        ax.legend()
        ax.set_xlabel(r"Right Ascension $[\deg]$")
        ax.set_ylabel(r"Declination $[\deg]$")
        plt.colorbar(
            mappable=a,
            ax=ax,
           
        )
        ax.grid()

        fig.set_tight_layout(True)

        return fig

    def summary(self):
        # TODO docstring
        print()
        print("   Batch Summary:")
        print(f"1. Batch includes {len(self._MPC_codes)} minor planets:")
        print("  ", self.MPC_objects)
        satObs = len(self._table.query("observatory == @self.space_telescopes"))
        print(
            f"2. Batch includes {self.size} observations, including {satObs} "
            + "observations from space telescopes"
        )
        print(
            f"3. The observations range from {self._table.epochUTC.min()} "
            + f"to {self._table.epochUTC.max()}"
        )
        print(f"   In seconds TDB since J2000: {self.epoch_start} to {self.epoch_end}")
        print(
            f"   In Julian Days: "
            + f"{self._table.epoch.min()}"
            + f" to {self._table.epoch.max()}"
        )
        print(
            f"4. The batch contains observations from {len(self.observatories)} "
            + f"observatories, including {len(self.space_telescopes)} space telescopes"
        )
        print()

    def observatories_table(
        self,
        only_in_batch: bool = True,
        only_space_telescopes=False,
        include_positions: bool = False,
    ):
        # TODO docstring
        temp = self._observatory_info
        temp2 = self._table
        # temp2["observatory "] = temp2.observatory.astype(str)
        # temp["Code"] = temp.Code.astype(str)

        count_observations = (
            temp2.groupby("observatory")
            .count()
            .rename(columns={"number": "count"})
            .reset_index(drop=False)
            .loc[:, ["observatory", "count"]]
        )

        temp = pd.merge(
            left=temp,
            right=count_observations,
            left_on="Code",
            right_on="observatory",
            how="left",
        )
        if only_in_batch:
            temp = temp.query("Code == @self.observatories")
        if only_space_telescopes:
            temp = temp.query("Code == @self._MPC_space_telescopes")
        if not include_positions:
            temp = temp.loc[:, ["Code", "Name", "count"]]
        return temp


def create_default_angular_observation_settings(
    links_dict: Dict[str, observation.LinkDefinition]
) -> List[observation.ObservationSettings]:
    observation_settings_list = list()
    for link in list(links_dict.values()):
        observation_settings_list.append(observation.angular_position(link))
    return observation_settings_list


if __name__ == "__main__":
    pass
