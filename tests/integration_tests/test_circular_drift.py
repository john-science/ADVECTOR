"""
The purpose of this test is to ensure that the second-order taylor kernel advects particles along circular streamlines.
It should also show that the Eulerian kernel fails to do this.
We will use a small latitude/longitude scale in order to approximate cartesian behavior.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

import numpy as np
import xarray as xr
import pandas as pd
import matplotlib.pyplot as plt
import pyopencl as cl
from datetime import timedelta
from ADVECTOR.enums.advection_scheme import AdvectionScheme
from ADVECTOR.enums.forcings import Forcing
from ADVECTOR.kernel_wrappers.Kernel3D import Kernel3D, Kernel3DConfig
from ADVECTOR.kernel_wrappers.Kernel2D import Kernel2D, Kernel2DConfig

nx = 20
lon = np.linspace(-0.02, 0.02, nx * 2)  # .01 degrees at equator ~= 1 km
lat = np.linspace(-0.01, 0.01, nx)

LON, LAT = np.meshgrid(lon, lat)
mag = np.sqrt(LON ** 2 + LAT ** 2)
U = np.divide(-LAT, mag, out=np.zeros_like(LON), where=mag != 0)
V = np.divide(LON, mag, out=np.zeros_like(LON), where=mag != 0)


def compare_alg_drift_3d(initial_radius: float, plot=False):
    current = xr.Dataset(
        {
            "U": (["lat", "lon", "depth", "time"], U[:, :, np.newaxis, np.newaxis]),
            "V": (["lat", "lon", "depth", "time"], V[:, :, np.newaxis, np.newaxis]),
            "W": (["lat", "lon", "depth", "time"], np.zeros((*U.shape, 1, 1))),
            "bathymetry": (
                ["lat", "lon"],
                -1 * np.ones(U.shape),
            ),  # gotta make sure particle is above bathymetry
        },
        coords={
            "lon": lon,
            "lat": lat,
            "depth": [0],
            "time": [np.datetime64("2000-01-01")],
        },
    )

    p0 = pd.DataFrame(
        {
            "p_id": [0],
            "lon": [0],
            "lat": [initial_radius],
            "depth": [0],
            "radius": [0.001],
            "density": [1025],
            "corey_shape_factor": [1],
            "exit_code": [0],
        }
    )
    eddy_diffusivity = xr.Dataset(
        {
            "horizontal_diffusivity": ("z_hd", ([0])),
            "vertical_diffusivity": ("z_vd", ([0])),
        },  # neutral buoyancy
        coords={"z_hd": [0], "z_vd": [0]},
    )
    seawater_density = xr.Dataset(
        {
            "rho": (
                ["lat", "lon", "depth", "time"],
                np.full((1, 1, 1, 1), p0.density[0]),
            )
        },
        coords={
            "lon": [0],
            "lat": [0],
            "depth": [0],
            "time": [np.datetime64("2000-01-01")],
        },
    )

    dt = timedelta(seconds=30)
    time = pd.date_range(start="2000-01-01", end="2000-01-01T6:00:00", freq=dt)
    p0["release_date"] = time[0]
    p0 = xr.Dataset(p0.set_index("p_id"))
    save_every = 1

    euler, taylor = [
        Kernel3D(
            forcing_data={
                Forcing.current: current,
                Forcing.seawater_density: seawater_density,
            },
            p0=p0,
            advect_time=time,
            save_every=save_every,
            config=Kernel3DConfig(
                advection_scheme=scheme,
                eddy_diffusivity=eddy_diffusivity,
                max_wave_height=0,
                wave_mixing_depth_factor=0,
                windage_multiplier=None,
                wind_mixing_enabled=False,
            ),
            context=cl.create_some_context(),
        )
        .execute()
        .squeeze()
        for scheme in (AdvectionScheme.eulerian, AdvectionScheme.taylor2)
    ]

    if plot:
        plot_trajectory(euler, taylor, current, p0)
        plt.suptitle("3D Kernel")
    return euler, taylor


def plot_trajectory(euler, taylor, current, p0):
    plt.figure(figsize=(8, 4))
    ax = plt.axes()
    ax.quiver(current.lon, current.lat, current.U.squeeze(), current.V.squeeze())
    ax.plot(p0.lon, p0.lat, "go")

    for name, P in {"euler": euler, "taylor": taylor}.items():
        ax.plot(P.lon, P.lat, ".-", label=name, linewidth=2)
        ax.plot(P.isel(time=-1).lon, P.isel(time=-1).lat, "rs")
    plt.legend()
    plt.title("drift comparison in circular field")
    plt.show()


def compare_alg_drift_2d(initial_radius: float, plot=False):
    current = xr.Dataset(
        {
            "U": (["lat", "lon", "time"], U[:, :, np.newaxis]),
            "V": (["lat", "lon", "time"], V[:, :, np.newaxis]),
        },
        coords={
            "lon": lon,
            "lat": lat,
            "time": [np.datetime64("2000-01-01")],
        },
    )

    p0 = pd.DataFrame(
        {"p_id": [0], "lon": [0], "lat": [initial_radius], "exit_code": [0]}
    )

    dt = timedelta(seconds=30)
    time = pd.date_range(start="2000-01-01", end="2000-01-01T6:00:00", freq=dt)
    p0["release_date"] = time[0]
    p0 = xr.Dataset(p0.set_index("p_id"))
    save_every = 1

    euler, taylor = [
        Kernel2D(
            forcing_data={
                Forcing.current: current,
            },
            p0=p0,
            advect_time=time,
            save_every=save_every,
            config=Kernel2DConfig(
                advection_scheme=scheme,
                eddy_diffusivity=0,
                windage_coefficient=0,
            ),
            context=cl.create_some_context(),
        )
        .execute()
        .squeeze()
        for scheme in (AdvectionScheme.eulerian, AdvectionScheme.taylor2)
    ]

    if plot:
        plot_trajectory(euler, taylor, current, p0)
        plt.suptitle("2D Kernel")

    return euler, taylor


def test_circular_drift_2d():
    initial_radius = 0.005
    euler, taylor = compare_alg_drift_2d(initial_radius=initial_radius)

    # taylor stays within 5% of radius on average
    np.testing.assert_allclose(
        np.sqrt(taylor.lon ** 2 + taylor.lat ** 2).mean(), initial_radius, rtol=0.05
    )

    # euler spirals out wildly
    assert np.sqrt(euler.lon ** 2 + euler.lat ** 2)[-1] > initial_radius * 1.5


def test_circular_drift_3d():
    initial_radius = 0.005
    euler, taylor = compare_alg_drift_3d(initial_radius=initial_radius)

    # taylor stays within 5% of radius on average
    np.testing.assert_allclose(
        np.sqrt(taylor.lon ** 2 + taylor.lat ** 2).mean(), initial_radius, rtol=0.05
    )

    # euler spirals out wildly
    assert np.sqrt(euler.lon ** 2 + euler.lat ** 2)[-1] > initial_radius * 1.5


if __name__ == "__main__":
    euler, taylor = compare_alg_drift_2d(initial_radius=0.005, plot=True)
    euler, taylor = compare_alg_drift_3d(initial_radius=0.005, plot=True)
