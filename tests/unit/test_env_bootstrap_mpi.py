import os
from typing import Dict
from unittest.mock import patch

from subprocess_task import is_mpi_job


def test_is_mpi_job_ompi():
    """Test the MPI submission detection (OMPI flavor)."""
    env: Dict[str, str] = {"OMPI_COMM_WORLD_RANK": "2", "OMPI_COMM_WORLD_SIZE": "4"}
    with patch.dict(os.environ, env, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is True
        assert rank == 2


def test_is_mpi_job_pmi():
    """Test the MPI submission detection (Intel flavor)."""
    env: Dict[str, str] = {"PMI_RANK": "1", "PMI_SIZE": "2"}
    with patch.dict(os.environ, env, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is True
        assert rank == 1


def test_is_mpi_job_slurm():
    """Test the MPI submission detection (based on SLURM variables only)."""
    env: Dict[str, str] = {"SLURM_PROCID": "1", "SLURM_NTASKS": "4"}
    with patch.dict(os.environ, env, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is True
        assert rank == 1


def test_is_mpi_job_slurm_size_1():
    """Test non-MPI submission for SLURM_NTASKS <= 2."""
    env: Dict[str, str] = {"SLURM_PROCID": "0", "SLURM_NTASKS": "1"}
    with patch.dict(os.environ, env, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is False
        assert rank == 0


def test_is_mpi_job_slurm_size_2():
    """Test non-MPI submission for SLURM_NTASKS <= 2."""
    env: Dict[str, str] = {"SLURM_PROCID": "0", "SLURM_NTASKS": "2"}
    with patch.dict(os.environ, env, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is False
        assert rank == 0


def test_is_mpi_job_none():
    """Test non-MPI submission for no relevant environment variables."""
    with patch.dict(os.environ, {}, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is False
        assert rank == -1


def test_is_mpi_job_slurm_only_rank():
    """Test non-MPI submission for SLURM environment variable only."""
    env: Dict[str, str] = {"SLURM_PROCID": "0"}
    with patch.dict(os.environ, env, clear=True):
        is_mpi: bool
        rank: int
        is_mpi, rank = is_mpi_job()
        assert is_mpi is False
        assert rank == 0
